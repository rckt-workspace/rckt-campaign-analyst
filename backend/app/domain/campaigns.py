"""Campaign rules that do not need the database. Pure functions, no I/O."""

from datetime import date


class InvalidPeriodError(ValueError):
    """The period ends before it starts. Raised by schemas (422) and by updates that merge two dates."""


def validate_period(period_start: date | None, period_end: date | None) -> None:
    if period_start is not None and period_end is not None and period_end < period_start:
        raise InvalidPeriodError("period_end must not be before period_start")
