"""The selfcheck is both a user command and our simplest full-stack test:
if the shipped buggy fixture gets CONFIRMED, the engine works on this box."""

from exhibit_a.selfcheck import run_selfcheck


def test_selfcheck_confirms_shipped_bug():
    # Exit 0 means the fixture bug was reproduced end-to-end with no API key.
    assert run_selfcheck(verbose=False) == 0
