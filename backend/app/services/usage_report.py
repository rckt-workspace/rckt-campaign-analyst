"""Monthly usage report for one organization: read-only aggregates over the existing tables.

Tables read: analyses, analysis_usage, analysis_quotas, analysis_quota_usage. No writes, no provider calls.

Consistency: one read-only transaction at REPEATABLE READ, so the four reads see the same snapshot. Counts never join
analyses to analysis_usage and count analyses from the joined rows: analyses are counted from analyses, calls from
analysis_usage, and the join only filters calls by their analysis's month. Nothing is counted twice.
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Analysis, AnalysisQuota, AnalysisQuotaUsage, AnalysisUsage
from app.db.session import Database
from app.domain.schemas import (
    UsageAnalysesRead,
    UsageCallsRead,
    UsageCostRead,
    UsageQuotaRead,
    UsageReportRead,
    UsageTokensRead,
)
from app.domain.usage_report import UsagePeriod, cost_completeness

CALL_STATUSES = ("succeeded", "failed", "reserved", "unknown")
ANALYSIS_STATUSES = ("succeeded", "failed", "running")


@dataclass(frozen=True)
class _CallGroup:
    """Aggregates of the calls with one status, read from analysis_usage."""

    calls: int
    prompt_reported: int
    completion_reported: int
    prompt_sum: int | None
    completion_sum: int | None
    cost_reported: int
    cost_zero: int
    cost_sum: Decimal | None


async def usage_report(
    database: Database, *, organization_id: uuid.UUID, period: UsagePeriod, quota_enforced: bool
) -> UsageReportRead:
    async with database.session() as session:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        analyses, without_record = await _analyses(session, organization_id, period)
        calls = await _calls(session, organization_id, period)
        quota_row, usage_row, counted = await _quota(session, organization_id, period)

    requested = sum(analyses.values())
    call_total = sum(group.calls for group in calls.values())
    return UsageReportRead(
        organization_id=organization_id,
        period=period.label,
        period_start=period.start,
        period_end_exclusive=period.end,
        has_activity=requested > 0 or call_total > 0,
        analyses=UsageAnalysesRead(
            requested=requested,
            succeeded=analyses.get("succeeded", 0),
            failed=analyses.get("failed", 0),
            running=analyses.get("running", 0),
            without_quota_record=without_record,
        ),
        provider_calls=_calls_read(calls, call_total),
        quota=_quota_read(quota_row, usage_row, counted, quota_enforced),
    )


async def _analyses(session: AsyncSession, organization_id: uuid.UUID, period: UsagePeriod) -> tuple[dict[str, int], int]:
    """Analyses requested in the month (by created_at), per status, and how many predate quota records."""
    rows = (
        await session.execute(
            select(
                Analysis.status,
                func.count().label("requested"),
                func.count().filter(Analysis.quota_period_start.is_(None)).label("without_record"),
            )
            .where(
                Analysis.organization_id == organization_id,
                Analysis.created_at >= period.start_at,
                Analysis.created_at < period.end_at,
            )
            .group_by(Analysis.status)
        )
    ).all()
    per_status = {row.status: int(row.requested) for row in rows}
    without_record = sum(int(row.without_record) for row in rows)
    return per_status, without_record


async def _calls(session: AsyncSession, organization_id: uuid.UUID, period: UsagePeriod) -> dict[str, _CallGroup]:
    """Provider calls of the month's analyses, grouped by call status. Each call is counted once."""
    rows = (
        await session.execute(
            select(
                AnalysisUsage.status,
                func.count().label("calls"),
                func.count(AnalysisUsage.prompt_tokens).label("prompt_reported"),
                func.count(AnalysisUsage.completion_tokens).label("completion_reported"),
                func.sum(AnalysisUsage.prompt_tokens).label("prompt_sum"),
                func.sum(AnalysisUsage.completion_tokens).label("completion_sum"),
                func.count(AnalysisUsage.provider_cost).label("cost_reported"),
                func.count().filter(AnalysisUsage.provider_cost == 0).label("cost_zero"),
                func.sum(AnalysisUsage.provider_cost).label("cost_sum"),
            )
            .select_from(AnalysisUsage)
            .join(
                Analysis,
                and_(
                    Analysis.organization_id == AnalysisUsage.organization_id,
                    Analysis.id == AnalysisUsage.analysis_id,
                ),
            )
            .where(
                AnalysisUsage.organization_id == organization_id,
                Analysis.created_at >= period.start_at,
                Analysis.created_at < period.end_at,
            )
            .group_by(AnalysisUsage.status)
        )
    ).all()
    return {
        row.status: _CallGroup(
            calls=int(row.calls),
            prompt_reported=int(row.prompt_reported),
            completion_reported=int(row.completion_reported),
            prompt_sum=None if row.prompt_sum is None else int(row.prompt_sum),
            completion_sum=None if row.completion_sum is None else int(row.completion_sum),
            cost_reported=int(row.cost_reported),
            cost_zero=int(row.cost_zero),
            cost_sum=None if row.cost_sum is None else Decimal(row.cost_sum),
        )
        for row in rows
    }


async def _quota(session: AsyncSession, organization_id: uuid.UUID, period: UsagePeriod):  # type: ignore[no-untyped-def]
    """The organization's quota row (if any), this month's unit counters, and the analyses counted in this month."""
    quota_row = (
        await session.execute(
            select(AnalysisQuota.plan, AnalysisQuota.monthly_analysis_limit).where(
                AnalysisQuota.organization_id == organization_id
            )
        )
    ).one_or_none()
    usage_row = (
        await session.execute(
            select(AnalysisQuotaUsage.consumed, AnalysisQuotaUsage.released).where(
                AnalysisQuotaUsage.organization_id == organization_id,
                AnalysisQuotaUsage.period_start == period.start,
            )
        )
    ).one_or_none()
    counted = await session.scalar(
        select(func.count()).where(
            Analysis.organization_id == organization_id,
            Analysis.quota_period_start == period.start,
        )
    )
    return quota_row, usage_row, int(counted or 0)


def _calls_read(groups: dict[str, _CallGroup], total: int) -> UsageCallsRead:
    def count(status: str) -> int:
        return groups[status].calls if status in groups else 0

    reported = [g for g in groups.values()]
    prompt_known = [g.prompt_sum for g in reported if g.prompt_sum is not None]
    completion_known = [g.completion_sum for g in reported if g.completion_sum is not None]
    cost_known = [g.cost_sum for g in reported if g.cost_sum is not None]
    calls_with_cost = sum(g.cost_reported for g in reported)
    zero_cost = sum(g.cost_zero for g in reported)

    return UsageCallsRead(
        total=total,
        succeeded=count("succeeded"),
        failed=count("failed"),
        reserved=count("reserved"),
        unknown=count("unknown"),
        tokens=UsageTokensRead(
            # Null when no call reported the value: an unknown total is never presented as zero.
            prompt_total=sum(prompt_known) if prompt_known else None,
            completion_total=sum(completion_known) if completion_known else None,
            calls_with_prompt_reported=sum(g.prompt_reported for g in reported),
            calls_with_completion_reported=sum(g.completion_reported for g in reported),
        ),
        cost=UsageCostRead(
            known_total=sum(cost_known, Decimal("0")) if cost_known else None,
            calls_with_cost=calls_with_cost,
            calls_without_cost=total - calls_with_cost,
            explicit_zero_cost_calls=zero_cost,
            completeness=cost_completeness(total, calls_with_cost),
        ),
    )


def _quota_read(quota_row, usage_row, counted: int, enforced: bool) -> UsageQuotaRead:  # type: ignore[no-untyped-def]
    configured = quota_row is not None
    limit = quota_row.monthly_analysis_limit if configured else None
    consumed = int(usage_row.consumed) if usage_row is not None else 0
    released = int(usage_row.released) if usage_row is not None else 0
    available = configured and enforced
    return UsageQuotaRead(
        configured=configured,
        plan=quota_row.plan if configured else None,
        monthly_analysis_limit=limit,
        enforcement_enabled=enforced,
        analyses_counted=counted,
        units_consumed=consumed,
        units_released=released,
        reconciled=consumed + released == counted,
        remaining_available=available,
        # Only a configured limit under enforcement is a real remaining quota. Otherwise it is not reported at all.
        remaining=max(limit - consumed, 0) if available else None,
    )
