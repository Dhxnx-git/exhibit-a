"""Verdict decision logic + receipt-rendering safety.

Two concerns: (1) decide() maps engine results to the right status; (2) the
rendered receipt can't be weaponized by attacker-influenced text (mentions,
fence-breakouts)."""

from exhibit_a.schemas import (
    CandidateTest, GateCheck, ReproPlan, ValidationResult,
    CONFIRMED, UNREPRODUCIBLE, NEEDS_INFO, ENV_FAILED,
)
from exhibit_a.verdict import decide, render_receipt


def _plan(**kw):
    base = dict(title="t", symptom_kind="wrong_value", expected="a", actual="b",
                steps=["do x"])
    base.update(kw)
    return ReproPlan.from_dict(base)


def _passing_result():
    r = ValidationResult(candidate=CandidateTest(code="def test_x():\n assert False"))
    r.gates = [GateCheck("collects", True, ""), GateCheck("fails_on_head", True, ""),
               GateCheck("symptom_match", True, ""), GateCheck("stable", True, "")]
    return r


def _failed_result(stop="fails_on_head"):
    r = ValidationResult(candidate=CandidateTest(code="def test_x():\n assert True"))
    r.gates = [GateCheck("collects", True, ""), GateCheck(stop, False, "test passed")]
    return r


def test_confirmed_when_a_candidate_passes_all_gates():
    v = decide(_plan(), [_failed_result(), _passing_result()])
    assert v.status == CONFIRMED
    assert v.winner is not None


def test_unreproducible_when_all_fail():
    v = decide(_plan(), [_failed_result(), _failed_result()])
    assert v.status == UNREPRODUCIBLE


def test_needs_info_when_plan_too_vague():
    vague = ReproPlan.from_dict({"title": "t", "symptom_kind": "other",
                                 "expected": "a", "actual": "b"})
    assert decide(vague, []).status == NEEDS_INFO


def test_env_failed_short_circuits():
    v = decide(_plan(), [], env_ok=False, env_error="pip exploded")
    assert v.status == ENV_FAILED
    assert "pip exploded" in v.env_error


def test_no_plan_is_needs_info():
    assert decide(None, []).status == NEEDS_INFO


def test_receipt_neutralizes_at_mentions_in_observed_failure():
    # A test steered by a malicious issue might emit "@maintainer" in its
    # message. It must land inside a code fence so GitHub won't fire a ping.
    r = _passing_result()
    r.signatures = [__import__("exhibit_a.schemas", fromlist=["FailureSignature"])
                    .FailureSignature(True, "exception", exception_type="ValueError",
                                      message_excerpt="ping @everyone now")]
    md = render_receipt(decide(_plan(), [r]))
    # The mention exists only within a ```text fence.
    idx = md.index("@everyone")
    fence_before = md.rfind("```", 0, idx)
    fence_after = md.find("```", idx)
    assert fence_before != -1 and fence_after != -1


def test_receipt_strips_backticks_from_untrusted_text():
    r = _passing_result()
    r.signatures = [__import__("exhibit_a.schemas", fromlist=["FailureSignature"])
                    .FailureSignature(True, "exception", exception_type="ValueError",
                                      message_excerpt="```\n# breakout\n```")]
    md = render_receipt(decide(_plan(), [r]))
    # No raw backticks survived from the untrusted excerpt into the fence body:
    # the observed-failure section renders without the injected fence.
    observed = md.split("### Observed failure")[-1]
    assert "# breakout" in observed          # text preserved
    assert "```\n# breakout" not in observed  # but not as a real fence


def test_confirmed_receipt_contains_the_test_code():
    v = decide(_plan(), [_passing_result()])
    md = render_receipt(v)
    assert "def test_x" in md
    assert "Reproduced" in md
