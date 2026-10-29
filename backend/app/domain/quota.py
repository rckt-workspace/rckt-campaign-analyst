"""Analysis quotas: the monthly unit, the period it is counted in, and the refusals. No database access here.

Policy and reasons: docs/PHASE2_QUOTAS.md. A unit is consumed when an analysis is claimed and released only when no
provider call was ever reserved for it. Nothing here is a monetary limit.
"""

PLAN_VALUES = ("pilot", "standard", "enterprise")


class QuotaExceededError(Exception):
    """The organization has no analysis left in this UTC month. Nothing is claimed; nothing is called."""


class QuotaNotConfiguredError(Exception):
    """Enforcement is on and the organization has no quota row. Nothing is claimed; nothing is called."""
