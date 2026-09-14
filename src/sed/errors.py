"""Error types mapped to CLI exit codes.

Exit codes are a contract with agents and Task Scheduler wrappers:
0 ok, 2 validation errors (details as JSON), 3 busy after retries, 4 precondition failed.
"""

from __future__ import annotations

from typing import Any

EXIT_OK = 0
EXIT_VALIDATION = 2
EXIT_BUSY = 3
EXIT_PRECONDITION = 4


class SedError(Exception):
    exit_code = 1
    kind = "error"

    def __init__(self, message: str, details: Any | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "message": self.message}
        if self.details is not None:
            out["details"] = self.details
        return out


class ValidationFailed(SedError):
    exit_code = EXIT_VALIDATION
    kind = "validation"


class Busy(SedError):
    exit_code = EXIT_BUSY
    kind = "busy"


class PreconditionFailed(SedError):
    exit_code = EXIT_PRECONDITION
    kind = "precondition"
