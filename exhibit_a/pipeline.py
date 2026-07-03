"""The orchestrator: report in, Verdict out.

This wires the five stages together and owns the security-relevant control
flow (ENV_FAILED before we ever blame a reporter; hard iteration caps so a
confused model can't spend your money forever). It deliberately holds NO
GitHub credentials — posting is a separate concern (see action/post.py), so
the stage that reasons about hostile text has nothing worth stealing.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from .extract import build_plan
from .llm import LLMClient, LLMError
from .schemas import ReproPlan, SchemaError, Verdict, NEEDS_INFO
from .sourcepick import pick_sources
from .stage import (
    ExhibitConfig,
    create_venv,
    prepare_workspace,
    run_setup,
)
from .synthesize import build_candidates
from .validate import ValidationEngine
from .verdict import decide
from .runner import DockerRunner, LocalRunner


@dataclass
class PipelineOptions:
    max_candidates: int = 4
    check_known_good: bool = False
    use_docker: bool = False
    keep_workspace: bool = False


def run_pipeline(
    repo_root: Path,
    config: ExhibitConfig,
    plan: ReproPlan | None,
    client: LLMClient | None,
    issue_title: str = "",
    issue_body: str = "",
    options: PipelineOptions | None = None,
) -> Verdict:
    """Execute stages 2–5. Stage 1 (extract) runs here IF no plan was supplied.

    Passing a ready-made `plan` (and no client) is the fully-offline path used
    by tests and by maintainers who'd rather hand-write the plan than call an
    API. Same engine either way — that's the whole point of the typed seam.
    """
    options = options or PipelineOptions()

    # -- Stage 1: extract (only if we weren't handed a plan) -----------------
    if plan is None:
        if client is None:
            return decide(None, [], notes=["no plan and no LLM client provided"])
        try:
            plan = build_plan(client, issue_title, issue_body)
        except (LLMError, SchemaError) as e:
            return Verdict(status=NEEDS_INFO, plan=None,
                           notes=[f"could not extract a checkable plan: {e}"])

    work_root = Path(tempfile.mkdtemp(prefix="exhibit-work-"))
    try:
        # -- Stage 2: stage a throwaway workspace + set up env ---------------
        workspace = prepare_workspace(repo_root, work_root)

        if options.use_docker:
            if not config.docker_image:
                return decide(plan, [], env_ok=False,
                              env_error="--docker requested but no docker_image in .exhibit.toml")
            runner = DockerRunner(workspace, config.docker_image)
        else:
            venv = create_venv(workspace)
            runner = LocalRunner(workspace, venv_dir=venv)

        setup_ok, setup_results = run_setup(runner, config)
        if not setup_ok:
            last = setup_results[-1] if setup_results else None
            err = (f"setup command {last.argv} exited {last.exit_code}\n{last.stderr[-800:]}"
                   if last else "no setup commands ran")
            return decide(plan, [], env_ok=False, env_error=err)

        # -- Stage 3: synthesize candidate tests ----------------------------
        if client is None:
            # Offline mode requires pre-supplied candidates via the plan path;
            # with neither client nor candidates there is nothing to run.
            return decide(plan, [], notes=["no LLM client: cannot synthesize candidate tests. "
                                           "Provide candidates programmatically or run with --llm."])
        sources = pick_sources(workspace, plan)
        try:
            candidates = build_candidates(client, plan, sources, options.max_candidates)
        except (LLMError, SchemaError) as e:
            return decide(plan, [], notes=[f"synthesis failed: {e}"])
        if not candidates:
            return decide(plan, [], notes=["model produced no schema-valid candidate tests"])

        # -- Stages 4–5: validate each candidate through the gate gauntlet --
        engine = ValidationEngine(
            runner, config, workspace,
            repo_root=repo_root, check_known_good=options.check_known_good)
        attempts = []
        for i, cand in enumerate(candidates, 1):
            result = engine.validate(cand, plan, i)
            attempts.append(result)
            if result.ok:
                break  # first confirmed candidate wins; stop spending money
        return decide(plan, attempts)
    finally:
        if not options.keep_workspace:
            import shutil
            shutil.rmtree(work_root, ignore_errors=True)


def run_pipeline_with_candidates(
    repo_root: Path,
    config: ExhibitConfig,
    plan: ReproPlan,
    candidates,
    options: PipelineOptions | None = None,
) -> Verdict:
    """Fully-offline path: caller supplies both plan AND candidate tests.

    This is how the test-suite and the offline demo exercise the real engine
    end-to-end without an API key — proving the validation core, which is the
    novel part, stands entirely on its own.
    """
    options = options or PipelineOptions()
    work_root = Path(tempfile.mkdtemp(prefix="exhibit-work-"))
    try:
        workspace = prepare_workspace(repo_root, work_root)
        venv = create_venv(workspace)
        runner = LocalRunner(workspace, venv_dir=venv)
        setup_ok, setup_results = run_setup(runner, config)
        if not setup_ok:
            last = setup_results[-1] if setup_results else None
            err = (f"setup command {last.argv} exited {last.exit_code}\n{last.stderr[-800:]}"
                   if last else "no setup commands ran")
            return decide(plan, [], env_ok=False, env_error=err)
        engine = ValidationEngine(runner, config, workspace,
                                  repo_root=repo_root,
                                  check_known_good=options.check_known_good)
        attempts = []
        for i, cand in enumerate(candidates, 1):
            result = engine.validate(cand, plan, i)
            attempts.append(result)
            if result.ok:
                break
        return decide(plan, attempts)
    finally:
        if not options.keep_workspace:
            import shutil
            shutil.rmtree(work_root, ignore_errors=True)
