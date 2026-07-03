"""Verdict assembly and receipt rendering.

The receipt is the product. A maintainer should be able to read it and
distrust us productively: every gate, every rerun, every excerpt is there,
so "the bot says CONFIRMED" is never the load-bearing statement — the
attached failing test is.

Rendering rule for anything attacker-influenced (pytest output can contain
text from the issue that steered the test): it goes inside a fenced code
block, with backticks stripped so it cannot break OUT of the fence. GitHub
does not fire @-mentions from inside code fences, which kills the classic
"make the bot ping @everyone" prank as a side effect.
"""

from __future__ import annotations

from .schemas import (
    CONFIRMED,
    ENV_FAILED,
    NEEDS_INFO,
    UNREPRODUCIBLE,
    ReproPlan,
    ValidationResult,
    Verdict,
)

EXIT_CODES = {CONFIRMED: 0, UNREPRODUCIBLE: 10, NEEDS_INFO: 11, ENV_FAILED: 12}


def _fence(text: str, cap: int = 1200) -> str:
    """Attacker-influenced text goes in here and only here."""
    safe = (text or "").replace("`", "'")[:cap]
    return f"```text\n{safe}\n```"


def decide(plan: ReproPlan | None,
           attempts: list[ValidationResult],
           env_ok: bool = True,
           env_error: str = "",
           notes: list[str] | None = None) -> Verdict:
    """Deterministic verdict logic — small on purpose, tested to death."""
    notes = list(notes or [])
    if plan is None:
        return Verdict(status=NEEDS_INFO, plan=None, notes=notes or
                       ["the report could not be converted into a checkable plan"])
    if not env_ok:
        return Verdict(status=ENV_FAILED, plan=plan, env_error=env_error, notes=notes)
    # "Not enough to act on" beats running doomed candidates.
    if not plan.steps and not plan.affected_symbols and not plan.exception_type:
        return Verdict(status=NEEDS_INFO, plan=plan, notes=notes)
    winner = next((a for a in attempts if a.ok), None)
    if winner:
        return Verdict(status=CONFIRMED, plan=plan, winner=winner,
                       attempts=attempts, notes=notes)
    return Verdict(status=UNREPRODUCIBLE, plan=plan, attempts=attempts, notes=notes)


def render_receipt(verdict: Verdict, tool_version: str = "0.1.0") -> str:
    """The markdown that gets posted to the issue / printed to the console."""
    lines: list[str] = []
    p = verdict.plan

    if verdict.status == CONFIRMED:
        lines.append("## ✅ Reproduced. Failing test attached.")
    elif verdict.status == UNREPRODUCIBLE:
        lines.append("## ❌ Could not reproduce. Receipts below.")
    elif verdict.status == NEEDS_INFO:
        lines.append("## ℹ️ Not enough information to attempt a reproduction")
    else:
        lines.append("## ⚠️ Environment setup failed. No verdict on the report itself.")
    lines.append("")

    if p:
        lines.append(f"**Report as understood:** {p.title}")
        lines.append(f"**Symptom:** `{p.symptom_kind}`"
                     + (f" (`{p.exception_type}`)" if p.exception_type else ""))
        lines.append("")

    if verdict.status == CONFIRMED and verdict.winner:
        w = verdict.winner
        lines.append(f"The test below **fails on the current code** and did so on "
                     f"{sum(1 for s in w.signatures if s.failed)}/{len(w.signatures)} runs. "
                     "It should pass once the bug is fixed; it asserts the *expected* behavior.")
        lines.append("")
        lines.append("```python")
        lines.append(w.candidate.code.replace("```", "'''").rstrip())
        lines.append("```")
        lines.append("")
        lines.append("### Gates")
        lines.append("")
        lines.append("| gate | result | detail |")
        lines.append("|---|---|---|")
        for g in w.gates:
            detail = (g.detail or "").replace("`", "'").replace("|", "\\|").replace("\n", " ")[:200]
            lines.append(f"| {g.name} | {'pass' if g.ok else 'FAIL'} | {detail} |")
        lines.append("")
        if w.signatures:
            lines.append("### Observed failure")
            lines.append("")
            lines.append(_fence(f"{w.signatures[0].exception_type or w.signatures[0].error_kind}: "
                                f"{w.signatures[0].message_excerpt}"))

    if verdict.status == UNREPRODUCIBLE:
        lines.append(f"{len(verdict.attempts)} candidate test(s) were generated and executed; "
                     "none demonstrated the reported failure. Full attempt log:")
        lines.append("")
        for i, a in enumerate(verdict.attempts, 1):
            lines.append(f"<details><summary>Attempt {i}"
                         f", stopped at gate: {next((g.name for g in a.gates if not g.ok), 'n/a')}"
                         "</summary>")
            lines.append("")
            for g in a.gates:
                mark = "pass" if g.ok else "FAIL"
                detail = (g.detail or "").replace("`", "'").replace("\n", " ")[:200]
                lines.append(f"- **{g.name}**: {mark}. {detail}")
            lines.append("")
            lines.append("```python")
            lines.append(a.candidate.code.replace("```", "'''").rstrip()[:3000])
            lines.append("```")
            lines.append("")
            lines.append("</details>")
        lines.append("")
        lines.append("**Reporter:** the fastest way to get this confirmed is to answer:")
        questions = (verdict.plan.needs_info if verdict.plan and verdict.plan.needs_info
                     else ["What exact command/code triggers this, copy-pasteable?",
                           "What version/commit are you on?"])
        for q in questions:
            lines.append(f"- {q.replace('`', chr(39))}")

    if verdict.status == NEEDS_INFO:
        lines.append("The report doesn't contain enough concrete detail to attempt a "
                     "reproduction (no steps, no named function, no exception type).")
        lines.append("")
        for q in (p.needs_info if p and p.needs_info else
                  ["What exact input/command triggers the problem?",
                   "What did you expect, and what happened instead (full error text)?"]):
            lines.append(f"- {q.replace('`', chr(39))}")

    if verdict.status == ENV_FAILED:
        lines.append("Setup commands from `.exhibit.toml` failed, so no reproduction was "
                     "attempted. **This is a repo/config problem, not the reporter's fault.**")
        lines.append("")
        lines.append(_fence(verdict.env_error))

    if verdict.notes:
        lines.append("")
        for n in verdict.notes:
            lines.append(f"> note: {n.replace('`', chr(39))}")

    lines.append("")
    lines.append(f"<sub>exhibit-a v{tool_version}. verdicts are computed by a deterministic "
                 "gate engine; the LLM only drafts tests, it never grades them. "
                 "[how it works](https://github.com/Dhxnx-git/exhibit-a#how-it-works)</sub>")
    return "\n".join(lines)
