"""A tiny billing helper with one honest-to-goodness bug.

This package exists so you can watch exhibit-a reproduce a real bug on a real
(if small) codebase, offline, in about 20 seconds. Fix the bug and re-run to
watch the confirmed test flip to passing.
"""


def split_bill(total_cents: int, people: int) -> list[int]:
    """Split a bill of `total_cents` across `people`.

    BUG: floor division drops the remainder, so the splits can sum to LESS
    than the total. split_bill(100, 3) -> [33, 33, 33] == 99. A cent vanished.

    The fix (don't peek if you want to see exhibit-a catch it): distribute the
    leftover cents one-per-person until they're gone, so the splits always add
    back up to total_cents.
    """
    base = total_cents // people
    return [base] * people


def slugify(text: str) -> str:
    """Turn a title into a URL slug. (Correct — here so the repo isn't ALL bugs.)"""
    return "-".join(text.lower().split())
