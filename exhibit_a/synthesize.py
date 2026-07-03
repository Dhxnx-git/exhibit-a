"""Stage 3 — draft candidate failing tests from the plan + a slice of the repo.

Second and last LLM stage. It gets the structured plan (not the raw issue —
the untrusted prose stops at extraction) plus a read-only slice of the target
repo's source, and drafts pytest tests that SHOULD fail because of the bug.

"Should" is doing zero work here: whatever it drafts goes straight into the
validation engine, which fails the candidate unless it fails on HEAD, for the
right reason, repeatably. Synthesis is allowed to be wrong; it just can't be
wrong AND believed.
"""

from __future__ import annotations

from .llm import LLMClient
from .schemas import CandidateTest, ReproPlan, SchemaError

TESTS_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "rationale": {"type": "string",
                                  "description": "Why this should fail on the buggy code."},
                    "code": {"type": "string",
                             "description": "A complete pytest module: imports + one or more "
                                            "test_* functions. Self-contained."},
                },
                "required": ["code"],
            },
        }
    },
    "required": ["candidates"],
}

_SYSTEM = """You are the test-synthesis stage of a bug-reproduction tool.

Given a STRUCTURED bug plan and a slice of the target repository's source, \
write pytest tests that FAIL on the current (buggy) code and would PASS once \
the bug is fixed. Assert the EXPECTED behavior, so the test is a real \
regression test, not just `pytest.raises(TheBug)`.

Rules:
- Each candidate is a complete, self-contained pytest module (imports included).
- Import from the real package; do not redefine the code under test.
- No network, no sleeps, no file writes outside tmp_path, no subprocess.
- Prefer several small, different candidates over one big one — different \
  entry points, different inputs. If one is wrong, another may be right.
- You are writing test code only. You have no other capabilities."""


def _slice_repo(source_excerpts: dict[str, str], max_chars: int = 12_000) -> str:
    """Render selected source files into the prompt, budget-capped."""
    out, used = [], 0
    for path, text in source_excerpts.items():
        chunk = f"### FILE: {path}\n```python\n{text}\n```\n"
        if used + len(chunk) > max_chars:
            chunk = chunk[: max_chars - used] + "\n... (truncated)\n"
            out.append(chunk)
            break
        out.append(chunk)
        used += len(chunk)
    return "\n".join(out)


def build_candidates(client: LLMClient, plan: ReproPlan,
                     source_excerpts: dict[str, str],
                     max_candidates: int = 4) -> list[CandidateTest]:
    """Draft up to max_candidates tests; skip any that fail our schema check."""
    user = (
        "STRUCTURED BUG PLAN (trusted — already sanitized from the raw report):\n"
        f"- title: {plan.title}\n"
        f"- symptom_kind: {plan.symptom_kind}\n"
        f"- expected: {plan.expected}\n"
        f"- actual: {plan.actual}\n"
        f"- exception_type: {plan.exception_type}\n"
        f"- steps: {plan.steps}\n"
        f"- affected_symbols: {plan.affected_symbols}\n"
        f"- symptom_keywords: {plan.symptom_keywords}\n\n"
        f"RELEVANT SOURCE:\n{_slice_repo(source_excerpts)}\n\n"
        f"Write up to {max_candidates} candidate failing tests."
    )
    raw = client.complete_json(_SYSTEM, user, "submit_candidate_tests",
                               TESTS_TOOL_SCHEMA, max_tokens=8192)
    items = raw.get("candidates", [])
    if not isinstance(items, list):
        raise SchemaError("candidates must be a list")
    candidates: list[CandidateTest] = []
    for item in items[:max_candidates]:
        try:
            candidates.append(CandidateTest.from_dict(item))
        except SchemaError:
            continue  # a malformed candidate is dropped, not fatal
    return candidates
