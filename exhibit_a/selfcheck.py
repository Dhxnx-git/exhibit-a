"""`exhibit selfcheck` — prove the engine works on this machine, no API key.

We ship a tiny buggy package as string constants, write it to a temp repo,
and run the REAL validation pipeline against a REAL bug with a hand-written
plan + candidate test. If this prints CONFIRMED, then staging, venv creation,
`pip install -e .`, the gate gauntlet, and verdict rendering all work here —
independent of any language model.

The bug is a classic: split_bill divides with `//` and silently drops the
remainder cents, so the splits don't add up to the total. Expected behavior
(splits sum to the total) is asserted by the test, which therefore FAILS on
the buggy code — exactly the "wrong_value" shape the engine is built to catch.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .schemas import CandidateTest, ReproPlan, CONFIRMED
from .stage import ExhibitConfig
from .pipeline import run_pipeline_with_candidates, PipelineOptions
from .verdict import render_receipt

# --- the buggy fixture package (intentionally broken) ----------------------

FIXTURE_PYPROJECT = """\
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "buggy-calc"
version = "0.0.1"
requires-python = ">=3.11"

[tool.setuptools.packages.find]
include = ["buggy_calc*"]
"""

FIXTURE_SOURCE = '''\
"""A tiny billing helper with one honest-to-goodness bug."""


def split_bill(total_cents: int, people: int) -> list[int]:
    """Split a bill of `total_cents` across `people`.

    BUG: uses floor division and drops the remainder, so the returned splits
    can sum to LESS than total_cents. E.g. split_bill(100, 3) -> [33, 33, 33],
    which sums to 99. A cent vanished. The correct behavior distributes the
    remainder so the splits always sum back to the total.
    """
    base = total_cents // people
    return [base] * people
'''

# --- the hand-written repro artifacts (no LLM involved) --------------------

PLAN = ReproPlan(
    title="split_bill drops remainder cents; splits don't sum to total",
    symptom_kind="wrong_value",
    expected="sum(split_bill(total, n)) == total for all inputs",
    actual="sum(split_bill(100, 3)) == 99, not 100 — a cent disappears",
    steps=["call split_bill(100, 3)", "sum the result", "compare to 100"],
    symptom_keywords=["split_bill", "remainder"],
    affected_symbols=["split_bill"],
)

CANDIDATE = CandidateTest(
    label="sum-equals-total",
    rationale="The splits must sum to the original total; floor division breaks this.",
    code='''\
from buggy_calc import split_bill


def test_split_bill_conserves_total():
    total = 100
    splits = split_bill(total, 3)
    assert sum(splits) == total, f"expected splits to sum to {total}, got {sum(splits)}"
''',
)


def _write_fixture(root: Path) -> None:
    (root / "buggy_calc").mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(FIXTURE_PYPROJECT, encoding="utf-8")
    (root / "buggy_calc" / "__init__.py").write_text(FIXTURE_SOURCE, encoding="utf-8")
    (root / "tests").mkdir(exist_ok=True)


def run_selfcheck(verbose: bool = True) -> int:
    """Return 0 iff the engine CONFIRMS the shipped bug. Used by CLI + tests."""
    tmp = Path(tempfile.mkdtemp(prefix="exhibit-selfcheck-"))
    try:
        repo = tmp / "buggy-calc"
        repo.mkdir()
        _write_fixture(repo)
        config = ExhibitConfig(runs=2)  # 2 reruns keeps selfcheck snappy
        verdict = run_pipeline_with_candidates(
            repo, config, PLAN, [CANDIDATE], PipelineOptions())
        if verbose:
            print(render_receipt(verdict))
            print("\n" + "=" * 60)
            print(f"selfcheck verdict: {verdict.status}")
        ok = verdict.status == CONFIRMED and verdict.winner is not None
        if verbose:
            print("selfcheck: PASS ✅" if ok else "selfcheck: FAIL ❌")
        return 0 if ok else 1
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    # Mirror the CLI's console hardening so `python -m exhibit_a.selfcheck`
    # behaves the same as `exhibit selfcheck` on a cp1252 terminal.
    for _s in (__import__("sys").stdout, __import__("sys").stderr):
        _rc = getattr(_s, "reconfigure", None)
        if _rc:
            try:
                _rc(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
    raise SystemExit(run_selfcheck())
