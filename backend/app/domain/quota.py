"""Analysis quotas: the monthly unit, the period it is counted in, and the refusals. No database access here.

Policy and reasons: docs/PHASE2_QUOTAS.md. A unit is consumed when an analysis is claimed and released only when no
provider call was ever reserved for it. Nothing here is a monetary limit.
"""

from datetime import date, datetime, timezone

PLAN_VALUES = ("pilot", "standard", "enterprise")


class QuotaExceededError(Exception):
    """The organization has no analysis left in this UTC month. Nothing is claimed; nothing is called."""


class QuotaNotConfiguredError(Exception):
    """Enforcement is on and the organization has no quota row. Nothing is claimed; nothing is called."""


def quota_period_for(instant: datetime) -> date:
    """First day of the UTC month that contains the instant.

    A naive datetime is refused: its month would depend on the server's local zone, which quotas must not depend on.
    """
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("a quota period needs a timezone-aware instant")
    utc = instant.astimezone(timezone.utc)
    return date(utc.year, utc.month, 1)
