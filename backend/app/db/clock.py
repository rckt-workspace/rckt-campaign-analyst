"""The one clock of quota periods: PostgreSQL's transaction time, converted to UTC inside the database.

Why this and not the Python clock: an analysis has two timestamps, its creation and its quota month. Both must come from
the same instant, or a request near a month boundary can be reported in one month and counted in another. now() is the
start of the current transaction, so every statement of the claim transaction sees the same value, and created_at (set
by the same transaction) and the quota month cannot disagree.

Why the explicit timezone('UTC', ...): timezone(zone, timestamptz) returns the UTC wall-clock time whatever the
session's TimeZone setting is. The month is therefore UTC even on a server or session configured with another zone.
"""

from sqlalchemy import Date, ColumnElement, cast, func


def utc_quota_period(instant: ColumnElement | object) -> ColumnElement:
    """First day of the UTC month of a timestamptz value, as a date: the quota period of that instant."""
    return cast(func.date_trunc("month", func.timezone("UTC", instant)), Date)
