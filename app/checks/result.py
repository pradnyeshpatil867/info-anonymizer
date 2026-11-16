"""Shared result type for all checks.

Every check returns a CheckResult. The review screen uses `status`
to colour a field: OK = green, WARN = yellow, FAIL = red.
"""

from dataclasses import dataclass
from enum import Enum


class Status(str, Enum):
    OK = "ok"      # proven correct
    WARN = "warn"  # repaired or uncertain: the user must confirm
    FAIL = "fail"  # a check failed: the user must correct the field


@dataclass(frozen=True)
class CheckResult:
    name: str       # short id of the check, for example "mrz_birth_date"
    status: Status
    message: str    # plain-English text for the review screen

    @property
    def ok(self) -> bool:
        return self.status is Status.OK


def worst(results: list[CheckResult]) -> Status:
    """Return the worst status in a list (FAIL > WARN > OK)."""
    statuses = {r.status for r in results}
    if Status.FAIL in statuses:
        return Status.FAIL
    if Status.WARN in statuses:
        return Status.WARN
    return Status.OK
