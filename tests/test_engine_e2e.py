"""End-to-end through the REAL engine: build a tiny repo on disk, stage it,
create a venv, install it, run pytest, gate the result, render a verdict.

These are the tests that matter most — they prove the novel part (the
deterministic validation gauntlet) actually distinguishes a real repro from
a passing test, from a broken test, from a flaky test. They shell out and
create venvs, so they're slower; that's the price of testing the thing that
actually ships.
"""

import sys
import textwrap
from pathlib import Path

import pytest

from exhibit_a.schemas import (
    CandidateTest, ReproPlan, CONFIRMED, UNREPRODUCIBLE,
)
from exhibit_a.stage import ExhibitConfig
from exhibit_a.pipeline import run_pipeline_with_candidates, PipelineOptions

# Creating a venv + pip install per test is real work — these are the slow,
# high-value tests. They need a working Python build toolchain on the box.
def _make_repo(root: Path, source: str, pkg="widget") -> Path:
    (root / pkg).mkdir(parents=True)
    (root / pkg / "__init__.py").write_text(textwrap.dedent(source), encoding="utf-8")
    (root / "pyproject.toml").write_text(textwrap.dedent(f"""
        [build-system]
        requires = ["setuptools>=68"]
        build-backend = "setuptools.build_meta"
        [project]
        name = "{pkg}"
        version = "0.0.1"
        requires-python = ">=3.11"
        [tool.setuptools.packages.find]
        include = ["{pkg}*"]
    """), encoding="utf-8")
    (root / "tests").mkdir()
    return root


def _cfg():
    # runs=2 keeps e2e fast while still exercising the rerun/flake gate.
    return ExhibitConfig(runs=2, timeout_seconds=180)


def test_confirms_a_real_wrong_value_bug(tmp_path):
    repo = _make_repo(tmp_path / "repo", """
        def add(a, b):
            return a - b   # the bug: subtraction masquerading as addition
    """)
    plan = ReproPlan.from_dict({
        "title": "add() returns wrong result", "symptom_kind": "wrong_value",
        "expected": "add(2, 3) == 5", "actual": "add(2, 3) == -1",
        "steps": ["call add(2, 3)"], "affected_symbols": ["add"],
    })
    cand = CandidateTest(code=textwrap.dedent("""
        from widget import add
        def test_add():
            assert add(2, 3) == 5
    """))
    v = run_pipeline_with_candidates(repo, _cfg(), plan, [cand], PipelineOptions())
    assert v.status == CONFIRMED
    assert v.winner is not None
    assert all(g.ok for g in v.winner.gates)


def test_passing_test_is_not_a_repro(tmp_path):
    # The code is CORRECT; a test that passes proves nothing. Must be UNREPRODUCIBLE.
    repo = _make_repo(tmp_path / "repo", """
        def add(a, b):
            return a + b
    """)
    plan = ReproPlan.from_dict({
        "title": "add is broken", "symptom_kind": "wrong_value",
        "expected": "sums", "actual": "wrong", "steps": ["call add"],
        "affected_symbols": ["add"],
    })
    cand = CandidateTest(code=textwrap.dedent("""
        from widget import add
        def test_add():
            assert add(2, 3) == 5
    """))
    v = run_pipeline_with_candidates(repo, _cfg(), plan, [cand], PipelineOptions())
    assert v.status == UNREPRODUCIBLE
    # It stopped at fails_on_head — the test passed.
    stop = next(g.name for g in v.attempts[0].gates if not g.ok)
    assert stop == "fails_on_head"


def test_broken_test_is_rejected_as_infra_not_repro(tmp_path):
    # Candidate imports something that doesn't exist. That's a broken TEST,
    # and must NOT be reported as a confirmed bug.
    repo = _make_repo(tmp_path / "repo", """
        def add(a, b):
            return a - b   # genuinely buggy, but...
    """)
    plan = ReproPlan.from_dict({
        "title": "add wrong", "symptom_kind": "wrong_value",
        "expected": "5", "actual": "-1", "steps": ["call add"],
        "affected_symbols": ["add"],
    })
    cand = CandidateTest(code=textwrap.dedent("""
        from widget import add, subtract_that_does_not_exist  # ImportError
        def test_add():
            assert add(2, 3) == 5
    """))
    v = run_pipeline_with_candidates(repo, _cfg(), plan, [cand], PipelineOptions())
    assert v.status == UNREPRODUCIBLE  # the buggy code is real, but THIS test is broken
    stop = next(g.name for g in v.attempts[0].gates if not g.ok)
    assert stop in ("collects", "fails_on_head", "symptom_match")


def test_wrong_symptom_shape_is_rejected(tmp_path):
    # Report says "wrong value" (expects an assertion), but the test triggers
    # an actual exception. Right that something's off, wrong shape -> reject.
    repo = _make_repo(tmp_path / "repo", """
        def get(d, k):
            return d[k]   # raises KeyError on missing key
    """)
    plan = ReproPlan.from_dict({
        "title": "get returns wrong value", "symptom_kind": "wrong_value",
        "expected": "None for missing key", "actual": "wrong value returned",
        "steps": ["call get"], "affected_symbols": ["get"],
    })
    cand = CandidateTest(code=textwrap.dedent("""
        from widget import get
        def test_get():
            assert get({}, 'missing') is None   # actually raises KeyError
    """))
    v = run_pipeline_with_candidates(repo, _cfg(), plan, [cand], PipelineOptions())
    assert v.status == UNREPRODUCIBLE
    stop = next(g.name for g in v.attempts[0].gates if not g.ok)
    assert stop == "symptom_match"


def test_flaky_test_is_rejected_by_stability_gate(tmp_path):
    # A test that fails ~half the time must not earn CONFIRMED — flaky repros
    # are exactly what erodes maintainer trust in bots.
    repo = _make_repo(tmp_path / "repo", """
        def add(a, b):
            return a - b
    """)
    plan = ReproPlan.from_dict({
        "title": "add wrong", "symptom_kind": "wrong_value",
        "expected": "5", "actual": "-1", "steps": ["call add"],
        "affected_symbols": ["add"],
    })
    # Deterministically alternate: fail on even runs, pass on odd, keyed off a
    # counter file in the workspace so reruns diverge.
    cand = CandidateTest(code=textwrap.dedent("""
        import os, pathlib
        from widget import add
        def test_flaky(tmp_path):
            counter = pathlib.Path(os.environ.get('PYTEST_CURRENT_TEST', 'x')).name
            marker = pathlib.Path('.exhibit_flake_marker')
            n = int(marker.read_text()) if marker.exists() else 0
            marker.write_text(str(n + 1))
            assert (n % 2 == 1) or add(2, 3) == 5  # fails first run, passes second
    """))
    v = run_pipeline_with_candidates(
        repo, ExhibitConfig(runs=3, timeout_seconds=180), plan, [cand], PipelineOptions())
    assert v.status == UNREPRODUCIBLE
    stop = next(g.name for g in v.attempts[0].gates if not g.ok)
    assert stop == "stable"


def test_env_failure_blames_config_not_reporter(tmp_path):
    # Setup command that can't succeed -> ENV_FAILED, never UNREPRODUCIBLE.
    repo = _make_repo(tmp_path / "repo", "def add(a, b): return a + b")
    cfg = ExhibitConfig(
        runs=2, timeout_seconds=60,
        setup=[[sys.executable, "-c", "import sys; sys.exit(7)"]],  # deliberate fail
    )
    plan = ReproPlan.from_dict({
        "title": "x", "symptom_kind": "wrong_value", "expected": "a",
        "actual": "b", "steps": ["y"], "affected_symbols": ["add"],
    })
    cand = CandidateTest(code="from widget import add\ndef test_x():\n assert add(1,1)==3")
    v = run_pipeline_with_candidates(repo, cfg, plan, [cand], PipelineOptions())
    assert v.status == "ENV_FAILED"
    assert "exited 7" in v.env_error or "exit" in v.env_error.lower()
