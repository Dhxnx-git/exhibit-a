"""Command-line interface.

Subcommands:
  exhibit init                 drop a starter .exhibit.toml in the repo
  exhibit run                  the real thing: report -> verdict
  exhibit selfcheck            run the built-in fixture end-to-end, no API key

`run` reads the issue from --issue-file (or --title/--body) and the repo from
--repo. With --llm it uses Anthropic for extract+synthesize; without it, it
expects --plan-file and --candidates-file (the fully-offline path), which is
also what selfcheck exercises.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .schemas import CandidateTest, ReproPlan, SchemaError


def _force_utf8_output() -> None:
    """Make stdout/stderr UTF-8 so receipts (which contain ✅/❌ and whatever
    Unicode a bug report throws at us) don't crash on a legacy console.

    Windows terminals still default to cp1252; a tool literally built to catch
    Unicode bugs should not fall over printing one. errors='replace' means a
    stray unencodable byte degrades to '?' instead of raising.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass  # detached/replaced stream — nothing we can do, don't crash
from .stage import load_config, write_starter_config, ConfigError
from .pipeline import (
    PipelineOptions,
    run_pipeline,
    run_pipeline_with_candidates,
)
from .verdict import render_receipt, EXIT_CODES


def _load_issue(args) -> tuple[str, str]:
    if args.issue_file:
        text = Path(args.issue_file).read_text(encoding="utf-8", errors="replace")
        # First markdown heading (if any) becomes the title; rest is the body.
        lines = text.splitlines()
        if lines and lines[0].lstrip().startswith("#"):
            return lines[0].lstrip("# ").strip(), "\n".join(lines[1:]).strip()
        return (args.title or "Bug report"), text
    return (args.title or "Bug report"), (args.body or "")


def _cmd_init(args) -> int:
    repo = Path(args.repo)
    path = repo / ".exhibit.toml"
    if path.exists() and not args.force:
        print(f".exhibit.toml already exists at {path} (use --force to overwrite)")
        return 1
    written = write_starter_config(repo)
    print(f"wrote {written}")
    print("edit the setup/test_command to match your project, then: exhibit run --repo . --issue-file bug.md --llm")
    return 0


def _cmd_run(args) -> int:
    repo = Path(args.repo).resolve()
    if not repo.exists():
        print(f"repo not found: {repo}", file=sys.stderr)
        return 2
    try:
        config = load_config(repo)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    options = PipelineOptions(
        max_candidates=args.max_candidates,
        check_known_good=args.check_known_good,
        use_docker=args.docker,
        keep_workspace=args.keep_workspace,
    )

    # -- fully-offline path: plan + candidates supplied on disk --------------
    if args.plan_file:
        try:
            plan = ReproPlan.from_dict(json.loads(Path(args.plan_file).read_text(encoding="utf-8")))
        except (SchemaError, json.JSONDecodeError, OSError) as e:
            print(f"bad --plan-file: {e}", file=sys.stderr)
            return 2
        if args.candidates_file:
            raw = json.loads(Path(args.candidates_file).read_text(encoding="utf-8"))
            candidates = [CandidateTest.from_dict(c) for c in raw]
            verdict = run_pipeline_with_candidates(repo, config, plan, candidates, options)
        else:
            # plan but no candidates -> needs an LLM to synthesize
            client = _make_client(args)
            if client is None:
                return 2
            verdict = run_pipeline(repo, config, plan, client, options=options)
    else:
        # -- LLM path: extract from the raw issue --------------------------
        client = _make_client(args)
        if client is None:
            return 2
        title, body = _load_issue(args)
        verdict = run_pipeline(repo, config, None, client,
                               issue_title=title, issue_body=body, options=options)

    receipt = render_receipt(verdict, tool_version=__version__)
    if args.json:
        print(verdict.to_json())
    else:
        print(receipt)
    if args.out:
        Path(args.out).write_text(receipt, encoding="utf-8")
    return EXIT_CODES.get(verdict.status, 1)


def _make_client(args):
    if not args.llm:
        print("this path needs the model (extract/synthesize). Re-run with --llm, "
              "or provide both --plan-file and --candidates-file for offline mode.",
              file=sys.stderr)
        return None
    from .llm import AnthropicClient, LLMError
    try:
        return AnthropicClient(model=args.model)
    except LLMError as e:
        print(f"LLM setup failed: {e}", file=sys.stderr)
        return None


def _cmd_selfcheck(args) -> int:
    """Run the shipped buggy-fixture end-to-end with NO API key.

    Proves the novel part (staging + setup + gate engine + verdict) works on
    this machine in ~20s. If this passes, the tool's core is sound even if you
    never call an LLM."""
    from .selfcheck import run_selfcheck
    return run_selfcheck(verbose=not args.quiet)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="exhibit",
        description="Turn bug reports into failing tests, or an honest receipt why not.")
    p.add_argument("--version", action="version", version=f"exhibit-a {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    pi = sub.add_parser("init", help="write a starter .exhibit.toml")
    pi.add_argument("--repo", default=".")
    pi.add_argument("--force", action="store_true")
    pi.set_defaults(func=_cmd_init)

    pr = sub.add_parser("run", help="reproduce a bug report against a repo")
    pr.add_argument("--repo", default=".", help="path to the target repository")
    pr.add_argument("--issue-file", help="markdown/text file containing the report")
    pr.add_argument("--title", help="issue title (if not using --issue-file)")
    pr.add_argument("--body", help="issue body (if not using --issue-file)")
    pr.add_argument("--plan-file", help="offline: a ReproPlan JSON to skip extraction")
    pr.add_argument("--candidates-file", help="offline: a JSON list of CandidateTest to skip synthesis")
    pr.add_argument("--llm", action="store_true", help="use Anthropic for extract/synthesize")
    pr.add_argument("--model", help="override model id (default: claude-sonnet-4-6)")
    pr.add_argument("--docker", action="store_true", help="run inside the configured docker image (network off for tests)")
    pr.add_argument("--max-candidates", type=int, default=4)
    pr.add_argument("--check-known-good", action="store_true",
                    help="also check the test passes at the reporter's known-good ref")
    pr.add_argument("--keep-workspace", action="store_true", help="don't delete the temp workspace (debug)")
    pr.add_argument("--json", action="store_true", help="print the full Verdict as JSON")
    pr.add_argument("--out", help="also write the markdown receipt to this path")
    pr.set_defaults(func=_cmd_run)

    ps = sub.add_parser("selfcheck", help="run the built-in fixture end-to-end (no API key)")
    ps.add_argument("--quiet", action="store_true")
    ps.set_defaults(func=_cmd_selfcheck)

    return p


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
