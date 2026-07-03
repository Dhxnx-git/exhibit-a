"""The junit parser is the deterministic heart — it decides assertion vs
exception vs infra, which is what makes symptom-matching meaningful. If this
is wrong, the whole 'fails for the RIGHT reason' guarantee is wrong."""

from pathlib import Path

from exhibit_a.validate import parse_junit


def _write(tmp_path: Path, xml: str) -> Path:
    p = tmp_path / "junit.xml"
    p.write_text(xml, encoding="utf-8")
    return p


def test_all_passed(tmp_path):
    sig = parse_junit(_write(tmp_path, """
        <testsuite><testcase classname="t" name="test_ok"/></testsuite>"""))
    assert not sig.failed
    assert sig.error_kind == "none"


def test_assertion_failure(tmp_path):
    sig = parse_junit(_write(tmp_path, """
        <testsuite><testcase classname="t" name="test_x">
        <failure message="AssertionError: assert 1 == 2">trace</failure>
        </testcase></testsuite>"""))
    assert sig.failed
    assert sig.error_kind == "assertion"


def test_bare_assert_message_is_assertion(tmp_path):
    sig = parse_junit(_write(tmp_path, """
        <testsuite><testcase classname="t" name="test_x">
        <failure message="assert 99 == 100">trace</failure>
        </testcase></testsuite>"""))
    assert sig.error_kind == "assertion"


def test_real_exception_is_exception(tmp_path):
    sig = parse_junit(_write(tmp_path, """
        <testsuite><testcase classname="t" name="test_x">
        <failure message="UnicodeEncodeError: 'ascii' codec ...">tb</failure>
        </testcase></testsuite>"""))
    assert sig.error_kind == "exception"
    assert sig.exception_type == "UnicodeEncodeError"


def test_collection_error_is_infra(tmp_path):
    # <error> (not <failure>) = pytest couldn't even run the test body.
    sig = parse_junit(_write(tmp_path, """
        <testsuite><testcase classname="t" name="test_x">
        <error message="ModuleNotFoundError: No module named 'nope'">tb</error>
        </testcase></testsuite>"""))
    assert sig.error_kind == "infra"
    assert sig.exception_type == "ModuleNotFoundError"


def test_import_error_in_failure_is_infra(tmp_path):
    # Even as a <failure>, an ImportError means the generated test is broken,
    # not that the library bug is real.
    sig = parse_junit(_write(tmp_path, """
        <testsuite><testcase classname="t" name="test_x">
        <failure message="ImportError: cannot import name 'gone'">tb</failure>
        </testcase></testsuite>"""))
    assert sig.error_kind == "infra"


def test_missing_file_is_infra_signature(tmp_path):
    sig = parse_junit(tmp_path / "does_not_exist.xml")
    assert sig.failed and sig.error_kind == "infra"


def test_unparseable_xml_is_infra(tmp_path):
    sig = parse_junit(_write(tmp_path, "<not valid xml"))
    assert sig.error_kind == "infra"


def test_stable_key_distinguishes_kinds(tmp_path):
    a = parse_junit(_write(tmp_path, """
        <testsuite><testcase classname="t" name="test_x">
        <failure message="AssertionError: x">tb</failure></testcase></testsuite>"""))
    b = parse_junit(_write(tmp_path, """
        <testsuite><testcase classname="t" name="test_x">
        <failure message="ValueError: y">tb</failure></testcase></testsuite>"""))
    assert a.stable_key() != b.stable_key()
