"""Monthly usage report: the period arithmetic and the completeness rules. Pure: no database, no clock of its own.

Semantics (docs/PHASE2_USAGE_REPORT.md):
- A period is one UTC calendar month, [first day 00:00 UTC, first day of the next month 00:00 UTC).
- An analysis belongs to the month of its created_at. Its provider calls belong to the same month as their analysis,
  even when a call's own timestamp falls in the next month.
- A cost that was not reported is unknown. It is never zero, and a total built from unknown costs says so.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

# The only accepted form: YYYY-MM with a plausible year. Shared with the route so both enforce the same rule.
PERIOD_PATTERN = r"^(20[2-9][0-9])-(0[1-9]|1[0-2])$"
_PERIOD = re.compile(PERIOD_PATTERN)


@dataclass(frozen=True)
class UsagePeriod:
    label: str  # YYYY-MM
    start: date  # first day of the month, UTC
    end: date  # first day of the next month, UTC (exclusive bound)

    @property
    def start_at(self) -> datetime:
        return datetime(self.start.year, self.start.month, 1, tzinfo=UTC)

    @property
    def end_at(self) -> datetime:
        return datetime(self.end.year, self.end.month, 1, tzinfo=UTC)


def parse_period(raw: str) -> UsagePeriod:
    match = _PERIOD.match(raw)
    if match is None:
        raise ValueError("period must be YYYY-MM")
    year, month = int(match.group(1)), int(match.group(2))
    start = date(year, month, 1)
    end = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return UsagePeriod(label=f"{year:04d}-{month:02d}", start=start, end=end)


def current_period(instant: datetime) -> UsagePeriod:
    """The UTC month of an aware instant. A naive instant is refused: its month would depend on the server zone."""
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("a period needs a timezone-aware instant")
    utc = instant.astimezone(UTC)
    return parse_period(f"{utc.year:04d}-{utc.month:02d}")


def cost_completeness(calls: int, attempts_with_cost: int) -> str:
    """How complete the known cost total is.

    no_calls: the month has no provider calls, so there is nothing to total.
    none: calls exist but none reported a cost; the total is unknown, not zero.
    complete: every call reported its cost.
    partial: some calls reported a cost and others did not; the total is a lower bound.
    """
    if calls == 0:
        return "no_calls"
    if attempts_with_cost == 0:
        return "none"
    if attempts_with_cost == calls:
        return "complete"
    return "partial"
