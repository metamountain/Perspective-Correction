"""Batch perspective correction for architectural photography."""
from .config import Settings          # noqa: F401
from .pipeline import process, analyse, Result, OK, SKIPPED, ERROR   # noqa: F401

# Bump on every change the user can see; the GUI shows it so a stale copy of
# this repo can be told apart from the live one at a glance.  The series starts
# at 1.0: 0.x was never shown anywhere, so no user ever saw it.
__version__ = "1.0"
__all__ = ["Settings", "process", "analyse", "Result", "OK", "SKIPPED", "ERROR"]
