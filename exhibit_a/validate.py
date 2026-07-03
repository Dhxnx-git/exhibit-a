"""The validation engine — the part of exhibit-a that gets to have opinions.

An LLM drafted a test and CLAIMS it reproduces the bug. Language models are
enthusiastic and occasionally full of it, so nothing here trusts the claim.
A candidate only earns CONFIRMED by passing every gate:

  G1 collects      pytest can even load the file (no syntax errors, etc.)
  G2 fails_on_head the test FAILS against the current code — a "repro" that
                   passes is just a unit test with delusions of grandeur
  G3 symptom_match it fails FOR THE REPORTED REASON — a UnicodeEncodeError
                   report is not confirmed by a test that dies on an import
  G4 stable        it fails the same way on every rerun (default 3) — flaky
                   repros are how maintainers stop trusting bots
  G5 known_good    (advisory) if the reporter named a version that worked,
                   the test should PASS there — true fail→pass evidence

Every gate records a human-readable `detail`, because the product isn't the
verdict — it's the receipts.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .schemas import (
    CandidateTest,
    FailureSignature,
    GateCheck,
    ReproPlan,
    ValidationResult,
)
from .stage import ExhibitConfig

# Exception types that mean "the generated TEST is broken", not "the bug is
# real". If the plan explicitly claims one of these as the bug (rare but
# legal — import machinery bugs exist), the plan's explicit claim wins.
INFRA_EXCEPTIONS = {
    "ImportError",
    "ModuleNotFoundError",
    "SyntaxError",
    "IndentationError",
    "NameError",
    "FileNotFoundError",  # generated tests love referencing files that don't exist
}


def parse_junit(xml_path: Path, plan_exception: str | None = None) -> FailureSignature:
    """Turn a pytest junit-xml file into a FailureSignature, deterministically.

    Why junit-xml instead of scraping terminal output: pytest's console text
    varies with plugins, terminal width, and verbosity. The XML is a stable,
    documented machine format — <failure> for a test that failed in its body,
    <error> for collection/fixture problems. Exactly the distinction G3 needs.
    """
    if not xml_path.exists():
        return FailureSignature(
            failed=True, error_kind="infra",
            message_excerpt="pytest produced no junit xml (crashed or usage error)",
        )
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError as e:
        return FailureSignature(failed=True, error_kind="infra",
                                message_excerpt=f"junit xml unparseable: {e}")

    failures: list[tuple[str, str]] = []  # (node_id, message)
    errors: list[tuple[str, str]] = []
    for case in root.iter("testcase"):
        node = f"{case.get('classname', '')}::{case.get('name', '')}"
        for f in case.iter("failure"):
            failures.append((node, f.get("message") or (f.text or "")))
        for f in case.iter("error"):
            errors.append((node, f.get("message") or (f.text or "")))

    if not failures and not errors:
        return FailureSignature(failed=False, error_kind="none")

    def first_exception_name(message: str) -> str | None:
        # pytest messages usually lead with the exception repr:
        #   "UnicodeEncodeError: 'ascii' codec can't encode ..."
        #   "AssertionError: assert 'x' == 'y'"
        head = message.strip().split(":", 1)[0].strip()
        # dotted names like "requests.exceptions.Timeout" are fine
        return head if head and head.replace(".", "").isidentifier() else None

    if errors:
        # Collection/fixture errors: the harness never even ran the test body.
        node, msg = errors[0]
        return FailureSignature(
            failed=True, error_kind="infra",
            exception_type=first_exception_name(msg),
            message_excerpt=msg[:500],
            failing_tests=[n for n, _ in errors],
        )

    node, msg = failures[0]
    exc = first_exception_name(msg)
    exc_leaf = exc.rsplit(".", 1)[-1] if exc else None

    if exc_leaf == "AssertionError" or (exc_leaf == "Failed"):
        # "Failed" is pytest.fail()/pytest.raises "DID NOT RAISE" — assertion-shaped.
        kind = "assertion"
    elif exc_leaf in INFRA_EXCEPTIONS and exc_leaf != (plan_exception or "").rsplit(".", 1)[-1]:
        kind = "infra"
    elif exc_leaf:
        kind = "exception"
    else:
        # Message didn't start with an exception name; a bare failed assert
        # ("assert result == ...") is the common cause.
        kind = "assertion" if msg.lstrip().startswith("assert") else "exception"

    return FailureSignature(
        failed=True, error_kind=kind, exception_type=exc,
        message_excerpt=msg[:500],
        failing_tests=[n for n, _ in failures],
    )


class ValidationEngine:
    """Runs one CandidateTest through the gate gauntlet inside a workspace."""

    def __init__(self, runner, config: ExhibitConfig, workspace: Path,
                 repo_root: Path | None = None, check_known_good: bool = False):
        self.runner = runner
        self.config = config
        self.workspace = Path(workspace)
        self.repo_root = Path(repo_root) if repo_root else None
        self.check_known_good = check_known_good

    # -- plumbing ------------------------------------------------------------

    def _write_test(self, candidate: CandidateTest, index: int) -> Path:
        """WE choose the filename. The model's suggestion is decoration.

        `test_exhibit_repro_*` both makes pytest collect it and makes it
        unmistakable in receipts which file the bot added.
        """
        test_dir = self.workspace / self.config.test_dir
        test_dir.mkdir(parents=True, exist_ok=True)
        path = test_dir / f"test_exhibit_repro_{index}.py"
        path.write_text(candidate.code, encoding="utf-8")
        return path

    def _test_argv(self, test_rel: str, extra: list[str] | None = None) -> list[str]:
        """Substitute {test_file} in the owner's test_command. Owner's argv,
        our relative path — nothing from the issue ever appears here."""
        argv = [a.replace("{test_file}", test_rel) for a in self.config.test_command]
        return argv + (extra or [])

    def _run_once(self, test_rel: str, index: int, run_no: int) -> FailureSignature:
        junit_rel = f".exhibit_junit_{index}_{run_no}.xml"
        argv = self._test_argv(test_rel, [f"--junit-xml={junit_rel}", "-p", "no:cacheprovider"])
        result = self.runner.run(argv, timeout_s=self.config.timeout_seconds,
                                 allow_network=False)
        if result.timed_out:
            return FailureSignature(failed=True, error_kind="timeout",
                                    message_excerpt=f"exceeded {self.config.timeout_seconds}s")
        return parse_junit(self.workspace / junit_rel, plan_exception=None)

    # -- gates ---------------------------------------------------------------

    def _gate_symptom(self, plan: ReproPlan, sig: FailureSignature) -> GateCheck:
        name = "symptom_match"
        if sig.error_kind == "timeout":
            ok = plan.symptom_kind == "hang"
            return GateCheck(name, ok, "timed out" + ("" if ok else " but report does not describe a hang"))
        if sig.error_kind == "infra":
            # One escape hatch: the reporter explicitly claims an infra-shaped
            # exception (e.g. a genuine ImportError bug in the library).
            claimed = (plan.exception_type or "").rsplit(".", 1)[-1]
            observed = (sig.exception_type or "").rsplit(".", 1)[-1]
            if claimed and claimed == observed:
                return GateCheck(name, True, f"{observed} matches the reported exception (explicit claim)")
            return GateCheck(name, False,
                             f"failure is test-infrastructure-shaped ({sig.exception_type or 'collection'}), "
                             "meaning the generated test is broken, not that the bug is real")
        if plan.exception_type:
            claimed = plan.exception_type.rsplit(".", 1)[-1]
            observed = (sig.exception_type or "").rsplit(".", 1)[-1]
            ok = claimed == observed
            return GateCheck(name, ok, f"reported {claimed}, observed {observed or 'no exception'}")
        if plan.symptom_kind == "wrong_value":
            ok = sig.error_kind == "assertion"
            return GateCheck(name, ok,
                             "wrong-value report requires an assertion failure, "
                             f"observed {sig.error_kind}")
        if plan.symptom_kind in ("exception", "crash"):
            if sig.error_kind != "exception":
                return GateCheck(name, False, f"report describes a crash, observed {sig.error_kind}")
            if plan.symptom_keywords:
                hit = [k for k in plan.symptom_keywords
                       if k.lower() in sig.message_excerpt.lower()
                       or k.lower() in (sig.exception_type or "").lower()]
                ok = bool(hit)
                return GateCheck(name, ok,
                                 f"keyword overlap with report: {hit or 'none'}")
            return GateCheck(name, True, f"raises {sig.exception_type} (no specific type reported)")
        # symptom_kind == "other": any honest (non-infra) failure counts
        return GateCheck(name, True, f"non-infra failure ({sig.error_kind}) accepted for 'other'")

    def _gate_known_good(self, plan: ReproPlan, candidate: CandidateTest,
                         index: int) -> GateCheck:
        """Advisory gate: at the reporter's known-good ref the test should PASS.

        Advisory means the outcome lands in the receipts but never blocks a
        CONFIRMED — re-running setup in an old worktree fails for too many
        boring reasons (deps drift, lockfiles) to make it load-bearing in v1.
        """
        name = "known_good (advisory)"
        if not plan.known_good_ref:
            return GateCheck(name, True, "no known-good ref reported; skipped")
        if not self.check_known_good:
            return GateCheck(name, True, "skipped (enable with --check-known-good)")
        if not self.repo_root or not (self.repo_root / ".git").exists():
            return GateCheck(name, True, "not a git checkout; skipped")

        import shutil as _shutil
        import subprocess as _sp
        import tempfile as _tmp

        tmpdir = Path(_tmp.mkdtemp(prefix="exhibit-worktree-"))
        try:
            add = _sp.run(["git", "-C", str(self.repo_root), "worktree", "add",
                           "--detach", str(tmpdir), plan.known_good_ref],
                          capture_output=True, text=True, timeout=120)
            if add.returncode != 0:
                return GateCheck(name, True, f"could not check out {plan.known_good_ref!r}; skipped")
            test_dir = tmpdir / self.config.test_dir
            test_dir.mkdir(parents=True, exist_ok=True)
            (test_dir / f"test_exhibit_repro_{index}.py").write_text(candidate.code, encoding="utf-8")
            runner = type(self.runner)(tmpdir) if type(self.runner).__name__ == "LocalRunner" else self.runner
            argv = self._test_argv(
                (Path(self.config.test_dir) / f"test_exhibit_repro_{index}.py").as_posix(),
                [f"--junit-xml=.exhibit_junit_kg.xml", "-p", "no:cacheprovider"])
            res = runner.run(argv, timeout_s=self.config.timeout_seconds, allow_network=False)
            sig = parse_junit(tmpdir / ".exhibit_junit_kg.xml")
            if not sig.failed:
                return GateCheck(name, True,
                                 f"test PASSES at {plan.known_good_ref}, true fail-then-pass evidence")
            return GateCheck(name, True,  # advisory: recorded, not blocking
                             f"test also fails at {plan.known_good_ref} "
                             f"({sig.error_kind}: {sig.exception_type}), regression claim not supported")
        except Exception as e:  # git missing, timeout, permissions — all advisory
            return GateCheck(name, True, f"skipped ({type(e).__name__}: {e})")
        finally:
            _sp.run(["git", "-C", str(self.repo_root), "worktree", "remove", "--force",
                     str(tmpdir)], capture_output=True, text=True)
            _shutil.rmtree(tmpdir, ignore_errors=True)

    # -- the gauntlet ----------------------------------------------------------

    def validate(self, candidate: CandidateTest, plan: ReproPlan, index: int) -> ValidationResult:
        result = ValidationResult(candidate=candidate)
        test_path = self._write_test(candidate, index)
        test_rel = test_path.relative_to(self.workspace).as_posix()
        result.test_path = test_rel

        # G1 — does pytest even collect the file?
        collect = self.runner.run(
            self._test_argv(test_rel, ["--collect-only", "-p", "no:cacheprovider"]),
            timeout_s=min(self.config.timeout_seconds, 120), allow_network=False)
        g1_ok = collect.exit_code == 0 and not collect.timed_out
        result.gates.append(GateCheck(
            "collects", g1_ok,
            "pytest collected the file" if g1_ok
            else f"collection failed (exit {collect.exit_code}): {collect.stderr[-300:] or collect.stdout[-300:]}"))
        if not g1_ok:
            return result

        # G2 — first real run: must FAIL, and not in an infra way.
        sig = self._run_once(test_rel, index, run_no=1)
        result.signatures.append(sig)
        g2_ok = sig.failed and sig.error_kind in ("assertion", "exception", "timeout")
        result.gates.append(GateCheck(
            "fails_on_head", g2_ok,
            f"{sig.error_kind}: {sig.exception_type or ''} {sig.message_excerpt[:160]}".strip()
            if sig.failed else "test PASSED, it does not demonstrate any bug"))
        if not g2_ok:
            return result

        # G3 — fails for the REPORTED reason.
        g3 = self._gate_symptom(plan, sig)
        result.gates.append(g3)
        if not g3.ok:
            return result

        # G4 — reruns must fail identically (flake gate).
        stable = True
        detail = f"failed identically on {self.config.runs}/{self.config.runs} runs"
        for run_no in range(2, self.config.runs + 1):
            sig_n = self._run_once(test_rel, index, run_no)
            result.signatures.append(sig_n)
            if sig_n.stable_key() != sig.stable_key():
                stable = False
                detail = (f"run {run_no} diverged: {sig_n.error_kind}/{sig_n.exception_type} "
                          f"vs {sig.error_kind}/{sig.exception_type}")
                break
        result.gates.append(GateCheck("stable", stable, detail))
        if not stable:
            return result

        # G5 — advisory fail→pass check at the reporter's known-good ref.
        result.gates.append(self._gate_known_good(plan, candidate, index))
        return result
