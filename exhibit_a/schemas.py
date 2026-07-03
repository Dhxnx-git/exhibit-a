"""Typed contracts for the whole pipeline.

Everything that flows between stages is one of these dataclasses. The rule
that keeps the tool safe lives here in spirit: fields extracted from an issue
(untrusted text from a stranger) are DATA. They get length-capped, validated,
and rendered — they are never handed to a shell, never used as a filename,
never treated as instructions. Commands come only from the repo owner's
.exhibit.toml.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any

# Caps applied to everything a stranger wrote. Not because long text is evil,
# but because uncapped attacker-controlled strings have a way of ending up in
# log files, comments, and prompts where they do not belong.
MAX_FIELD_CHARS = 2_000
MAX_LIST_ITEMS = 20

SYMPTOM_KINDS = ("exception", "wrong_value", "crash", "hang", "other")


class SchemaError(ValueError):
    """Raised when data (usually LLM output) doesn't match our contract."""


def _cap(value: str) -> str:
    """Trim any untrusted string to a sane length."""
    return value[:MAX_FIELD_CHARS]


def _req_str(d: dict, key: str) -> str:
    v = d.get(key)
    if not isinstance(v, str) or not v.strip():
        raise SchemaError(f"field '{key}' must be a non-empty string")
    return _cap(v.strip())


def _opt_str(d: dict, key: str) -> str | None:
    v = d.get(key)
    if v is None or v == "":
        return None
    if not isinstance(v, str):
        raise SchemaError(f"field '{key}' must be a string or null")
    return _cap(v.strip())


def _str_list(d: dict, key: str) -> list[str]:
    v = d.get(key, [])
    if not isinstance(v, list) or any(not isinstance(x, str) for x in v):
        raise SchemaError(f"field '{key}' must be a list of strings")
    return [_cap(x.strip()) for x in v if x.strip()][:MAX_LIST_ITEMS]


@dataclass
class ReproPlan:
    """What we believe the bug report is claiming, in machine-checkable form.

    Produced by the LLM extractor (or written by hand — the validation engine
    does not care who wrote it, which is exactly the point: the plan is an
    input to a deterministic machine, not a conversation).
    """

    title: str
    symptom_kind: str            # one of SYMPTOM_KINDS
    expected: str                # what the reporter says should happen
    actual: str                  # what the reporter says does happen
    steps: list[str] = field(default_factory=list)
    exception_type: str | None = None   # e.g. "UnicodeEncodeError" if known
    symptom_keywords: list[str] = field(default_factory=list)
    affected_symbols: list[str] = field(default_factory=list)  # fn/class names to look at
    known_good_ref: str | None = None   # git ref where the reporter says it worked
    needs_info: list[str] = field(default_factory=list)  # questions if underspecified

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ReproPlan":
        if not isinstance(d, dict):
            raise SchemaError("plan must be a JSON object")
        kind = _req_str(d, "symptom_kind")
        if kind not in SYMPTOM_KINDS:
            raise SchemaError(f"symptom_kind must be one of {SYMPTOM_KINDS}, got {kind!r}")
        plan = cls(
            title=_req_str(d, "title"),
            symptom_kind=kind,
            expected=_req_str(d, "expected"),
            actual=_req_str(d, "actual"),
            steps=_str_list(d, "steps"),
            exception_type=_opt_str(d, "exception_type"),
            symptom_keywords=_str_list(d, "symptom_keywords"),
            affected_symbols=_str_list(d, "affected_symbols"),
            known_good_ref=_opt_str(d, "known_good_ref"),
            needs_info=_str_list(d, "needs_info"),
        )
        # An exception_type like "IOError: rm -rf /" is nonsense — class names
        # are single identifiers. Reject anything shaped like a sentence.
        if plan.exception_type and not plan.exception_type.replace(".", "").isidentifier():
            raise SchemaError("exception_type must look like a Python exception class name")
        return plan

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CandidateTest:
    """One LLM-drafted (or hand-written) attempt at a failing test.

    `code` is written by US into a file WE name inside the workspace copy.
    Any filename the model suggests is treated as a label, nothing more.
    """

    code: str
    rationale: str = ""
    label: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CandidateTest":
        if not isinstance(d, dict):
            raise SchemaError("candidate must be a JSON object")
        code = d.get("code")
        if not isinstance(code, str) or "def test" not in code:
            raise SchemaError("candidate 'code' must be a string containing a test function")
        if len(code) > 20_000:
            raise SchemaError("candidate 'code' is implausibly large")
        return cls(
            code=code,
            rationale=_cap(str(d.get("rationale", ""))),
            label=_cap(str(d.get("label", ""))),
        )


@dataclass
class RunResult:
    """Raw outcome of one subprocess execution."""

    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False


@dataclass
class FailureSignature:
    """What we deterministically parsed out of one pytest run.

    error_kind meanings:
      assertion  — a test assert failed (the 'wrong value' shape)
      exception  — the code under test raised (the 'it crashes' shape)
      infra      — import/collection/fixture problems: the TEST is broken,
                   not the code under test. Never counts as a repro.
      timeout    — the run hit our wall clock. Matches 'hang' symptoms only.
      none       — everything passed.
    """

    failed: bool
    error_kind: str                      # assertion | exception | infra | timeout | none
    exception_type: str | None = None    # e.g. "UnicodeEncodeError"
    message_excerpt: str = ""
    failing_tests: list[str] = field(default_factory=list)  # junit node ids

    def stable_key(self) -> tuple:
        """Two runs 'failed the same way' iff these match (rerun gate)."""
        return (self.error_kind, self.exception_type, tuple(sorted(self.failing_tests)))


@dataclass
class GateCheck:
    """One named check in the validation engine, with a human-readable why."""

    name: str
    ok: bool
    detail: str


@dataclass
class ValidationResult:
    """Everything the engine concluded about one candidate test."""

    candidate: CandidateTest
    gates: list[GateCheck] = field(default_factory=list)
    signatures: list[FailureSignature] = field(default_factory=list)
    test_path: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.gates) and all(g.ok for g in self.gates)


# Verdict statuses. The CLI maps these to exit codes so CI can branch on them.
CONFIRMED = "CONFIRMED"
UNREPRODUCIBLE = "UNREPRODUCIBLE"
NEEDS_INFO = "NEEDS_INFO"
ENV_FAILED = "ENV_FAILED"


@dataclass
class Verdict:
    """The final answer, plus every receipt needed to distrust us productively."""

    status: str
    plan: ReproPlan | None
    winner: ValidationResult | None = None
    attempts: list[ValidationResult] = field(default_factory=list)
    env_error: str = ""
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        def enc(o: Any) -> Any:
            if hasattr(o, "__dataclass_fields__"):
                return asdict(o)
            raise TypeError(f"not JSON-serializable: {type(o)}")
        return json.dumps(asdict(self) if self.plan else {**asdict(self), "plan": None},
                          default=enc, indent=2)
