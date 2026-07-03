"""Symptom-matching gate logic (G3) in isolation.

This is where 'the test fails for the reason the report described' actually
lives. We drive _gate_symptom directly with synthetic signatures so we can
assert every branch without spinning subprocesses."""

from exhibit_a.schemas import FailureSignature, ReproPlan
from exhibit_a.stage import ExhibitConfig
from exhibit_a.validate import ValidationEngine


def _engine():
    # No runner/workspace needed: _gate_symptom is pure over its inputs.
    return ValidationEngine(runner=None, config=ExhibitConfig(), workspace=".")


def _plan(**kw):
    base = dict(title="t", symptom_kind="other", expected="a", actual="b")
    base.update(kw)
    return ReproPlan.from_dict(base)


def test_wrong_value_needs_assertion():
    eng = _engine()
    plan = _plan(symptom_kind="wrong_value")
    assert eng._gate_symptom(plan, FailureSignature(True, "assertion")).ok
    assert not eng._gate_symptom(plan, FailureSignature(True, "exception")).ok


def test_crash_needs_exception_not_assertion():
    eng = _engine()
    plan = _plan(symptom_kind="crash")
    assert eng._gate_symptom(plan, FailureSignature(True, "exception",
                                                    exception_type="ValueError")).ok
    assert not eng._gate_symptom(plan, FailureSignature(True, "assertion")).ok


def test_named_exception_must_match():
    eng = _engine()
    plan = _plan(symptom_kind="exception", exception_type="UnicodeEncodeError")
    ok = eng._gate_symptom(plan, FailureSignature(True, "exception",
                                                  exception_type="UnicodeEncodeError"))
    bad = eng._gate_symptom(plan, FailureSignature(True, "exception",
                                                   exception_type="ValueError"))
    assert ok.ok and not bad.ok


def test_named_exception_matches_on_leaf_of_dotted():
    eng = _engine()
    plan = _plan(symptom_kind="exception", exception_type="Timeout")
    sig = FailureSignature(True, "exception", exception_type="requests.exceptions.Timeout")
    assert eng._gate_symptom(plan, sig).ok


def test_infra_failure_never_matches_by_default():
    eng = _engine()
    for kind in ("wrong_value", "crash", "exception", "other"):
        plan = _plan(symptom_kind=kind)
        assert not eng._gate_symptom(plan, FailureSignature(True, "infra")).ok


def test_infra_can_match_only_if_explicitly_claimed():
    # A genuine ImportError bug in a library: reporter names it, so an infra
    # signature with the same type is allowed to count.
    eng = _engine()
    plan = _plan(symptom_kind="exception", exception_type="ImportError")
    sig = FailureSignature(True, "infra", exception_type="ImportError")
    assert eng._gate_symptom(plan, sig).ok


def test_timeout_only_matches_hang():
    eng = _engine()
    assert eng._gate_symptom(_plan(symptom_kind="hang"),
                             FailureSignature(True, "timeout")).ok
    assert not eng._gate_symptom(_plan(symptom_kind="crash"),
                                 FailureSignature(True, "timeout")).ok


def test_keyword_overlap_required_when_provided():
    eng = _engine()
    plan = _plan(symptom_kind="crash", symptom_keywords=["decode"])
    hit = FailureSignature(True, "exception", exception_type="ValueError",
                           message_excerpt="could not decode byte")
    miss = FailureSignature(True, "exception", exception_type="ValueError",
                            message_excerpt="index out of range")
    assert eng._gate_symptom(plan, hit).ok
    assert not eng._gate_symptom(plan, miss).ok
