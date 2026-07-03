"""Schema contracts: the first line of defense against a lying/steered model."""

import pytest

from exhibit_a.schemas import (
    CandidateTest,
    ReproPlan,
    SchemaError,
    MAX_FIELD_CHARS,
    MAX_LIST_ITEMS,
)


def test_minimal_valid_plan():
    plan = ReproPlan.from_dict({
        "title": "x breaks", "symptom_kind": "wrong_value",
        "expected": "a", "actual": "b",
    })
    assert plan.symptom_kind == "wrong_value"
    assert plan.steps == []


def test_rejects_unknown_symptom_kind():
    with pytest.raises(SchemaError):
        ReproPlan.from_dict({"title": "t", "symptom_kind": "banana",
                             "expected": "a", "actual": "b"})


def test_rejects_missing_required_field():
    with pytest.raises(SchemaError):
        ReproPlan.from_dict({"symptom_kind": "crash", "expected": "a", "actual": "b"})


def test_exception_type_must_be_identifier_shaped():
    # A model coerced by a malicious issue into stuffing a command here is
    # caught by the identifier check — "rm -rf /" is not a class name.
    with pytest.raises(SchemaError):
        ReproPlan.from_dict({
            "title": "t", "symptom_kind": "exception",
            "expected": "a", "actual": "b", "exception_type": "rm -rf /",
        })


def test_dotted_exception_type_ok():
    plan = ReproPlan.from_dict({
        "title": "t", "symptom_kind": "exception",
        "expected": "a", "actual": "b", "exception_type": "requests.exceptions.Timeout",
    })
    assert plan.exception_type == "requests.exceptions.Timeout"


def test_untrusted_strings_are_length_capped():
    huge = "A" * (MAX_FIELD_CHARS * 3)
    plan = ReproPlan.from_dict({
        "title": huge, "symptom_kind": "other", "expected": huge, "actual": huge,
    })
    assert len(plan.title) == MAX_FIELD_CHARS
    assert len(plan.expected) == MAX_FIELD_CHARS


def test_lists_are_item_capped():
    plan = ReproPlan.from_dict({
        "title": "t", "symptom_kind": "other", "expected": "a", "actual": "b",
        "steps": [f"step {i}" for i in range(MAX_LIST_ITEMS * 2)],
    })
    assert len(plan.steps) == MAX_LIST_ITEMS


def test_candidate_requires_a_test_function():
    with pytest.raises(SchemaError):
        CandidateTest.from_dict({"code": "x = 1  # no test here"})


def test_candidate_rejects_absurd_size():
    with pytest.raises(SchemaError):
        CandidateTest.from_dict({"code": "def test_x():\n    pass\n" + "#" * 25_000})


def test_plan_roundtrips_through_dict():
    d = {"title": "t", "symptom_kind": "crash", "expected": "a", "actual": "b",
         "affected_symbols": ["foo.bar"], "symptom_keywords": ["boom"]}
    plan = ReproPlan.from_dict(d)
    again = ReproPlan.from_dict(plan.to_dict())
    assert again.affected_symbols == ["foo.bar"]
