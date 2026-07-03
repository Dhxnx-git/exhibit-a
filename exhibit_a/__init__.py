"""exhibit-a — every bug report gets a failing test, or a receipt.

Public surface is intentionally small. The interesting, novel, fully-testable
part is the validation engine (validate.py) + verdict logic (verdict.py);
everything else is plumbing around it.
"""

from .schemas import (  # noqa: F401
    CandidateTest,
    ReproPlan,
    Verdict,
    CONFIRMED,
    UNREPRODUCIBLE,
    NEEDS_INFO,
    ENV_FAILED,
)

__version__ = "0.1.0"
