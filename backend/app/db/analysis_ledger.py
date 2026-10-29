"""Write protocol for analyses. Each function is one short transaction, and none stays open during a provider call.

Order for one request:
1. claim_analysis: commits the 'running' row (deterministic report, input hash, lease) and consumes one quota unit in
   the same transaction, before any provider call.
2. For each HTTP call: reserve_attempt commits, then the provider is called with no transaction open, then
   complete_attempt commits the outcome.
3. finalize_analysis commits the final state, and gives the unit back when no call was ever reserved.

A lease that expired is never resumed with a new call: abandon_expired marks the reserved attempts 'unknown' and the
analysis 'failed' with ANALYSIS_ABANDONED. The database enforces the rules (triggers in migration 0004); these
functions only sequence the writes and report what the database accepted or refused.

Guarantees and limits are documented in docs/PHASE2_ANALYSES.md, section 4, and the quota rules in docs/PHASE2_QUOTAS.md.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select, update
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.audit import insert_audit_event
from app.db.clock import utc_quota_period
from app.db.models import Analysis, AnalysisQuota, AnalysisQuotaUsage, AnalysisUsage
from app.db.session import Database
from app.domain.analysis import ABANDONED
from app.domain.audit import AuditDraft
from app.domain.quota import QuotaExceededError, QuotaNotConfiguredError
from app.domain.usage import ProviderUsage  # re-exported: callers import it from here too


@dataclass(frozen=True)
class ClaimResult:
    """What the claim found. 'claimed' is the only outcome that allows a provider call."""

    # claimed: this request owns a new running analysis.
    # existing: the key belongs to an analysis that is running (live lease) or completed. No call.
    # expired: the key belongs to a running analysis whose lease expired. Abandon it; never call again with this key.
    # reused: the key belongs to different input. Refused. No call.
    outcome: str
    analysis_id: uuid.UUID
    status: str
    error_code: str | None
    analysis_markdown: str | None
    model_used: str | None
    used_fallback: bool
    format_valid: bool | None


@dataclass(frozen=True)
class QuotaClaim:
    """Count this claim against the organization's monthly quota. The month is read inside the claim's transaction."""

    enforce: bool


async def claim_analysis(
    database: Database,
    *,
    organization_id: uuid.UUID,
    requested_by: uuid.UUID,
    campaign_id: uuid.UUID | None,
    metric_entry_id: uuid.UUID | None,
    idempotency_key: str,
    input_hash: str,
    prompt_version: str,
    model_requested: str,
    deterministic_report: dict,
    lease_seconds: int,
    quota: QuotaClaim | None = None,
) -> ClaimResult:
    """Inserts the running analysis, or finds the one that owns the key. Concurrent claims: exactly one inserts.

    With quota, a new claim also consumes one unit of its month in the same transaction. The month and created_at both come
    from PostgreSQL's transaction time (app/db/clock.py); the application clock is never consulted. A refused quota rolls the
    transaction back: no analysis, no unit, no event. A replay consumes nothing. Without quota, nothing is counted.
    """
    return await _claim(
        database,
        organization_id=organization_id,
        requested_by=requested_by,
        campaign_id=campaign_id,
        metric_entry_id=metric_entry_id,
        idempotency_key=idempotency_key,
        input_hash=input_hash,
        prompt_version=prompt_version,
        model_requested=model_requested,
        deterministic_report=deterministic_report,
        lease_seconds=lease_seconds,
        quota=quota,
        period=None,
    )


async def claim_analysis_in_period(
    database: Database,
    *,
    organization_id: uuid.UUID,
    requested_by: uuid.UUID,
    campaign_id: uuid.UUID | None,
    metric_entry_id: uuid.UUID | None,
    idempotency_key: str,
    input_hash: str,
    prompt_version: str,
    model_requested: str,
    deterministic_report: dict,
    lease_seconds: int,
    period: date,
    enforce: bool,
) -> ClaimResult:
    """For tests only: a claim into a fixed quota month, so month-specific rules are exercised without moving the database
    clock. Production code never calls it; it has no route and no service."""
    return await _claim(
        database,
        organization_id=organization_id,
        requested_by=requested_by,
        campaign_id=campaign_id,
        metric_entry_id=metric_entry_id,
        idempotency_key=idempotency_key,
        input_hash=input_hash,
        prompt_version=prompt_version,
        model_requested=model_requested,
        deterministic_report=deterministic_report,
        lease_seconds=lease_seconds,
        quota=QuotaClaim(enforce=enforce),
        period=period,
    )


async def _claim(
    database: Database,
    *,
    organization_id: uuid.UUID,
    requested_by: uuid.UUID,
    campaign_id: uuid.UUID | None,
    metric_entry_id: uuid.UUID | None,
    idempotency_key: str,
    input_hash: str,
    prompt_version: str,
    model_requested: str,
    deterministic_report: dict,
    lease_seconds: int,
    quota: QuotaClaim | None,
    period: date | None,
) -> ClaimResult:
    async with database.session() as session:
        if quota is not None and period is None:
            # The first statement fixes the transaction time: created_at below and this month are the same instant.
            period = await session.scalar(select(utc_quota_period(func.now())))
        if quota is not None:
            # The month row must exist before the analysis references it. Harmless if the claim later loses the race.
            await session.execute(
                pg_insert(AnalysisQuotaUsage)
                .values(organization_id=organization_id, period_start=period)
                .on_conflict_do_nothing()
            )
        claimed_id = (
            await session.execute(
                pg_insert(Analysis)
                .values(
                    organization_id=organization_id,
                    requested_by=requested_by,
                    campaign_id=campaign_id,
                    metric_entry_id=metric_entry_id,
                    idempotency_key=idempotency_key,
                    input_hash=input_hash,
                    prompt_version=prompt_version,
                    model_requested=model_requested,
                    deterministic_report=deterministic_report,
                    status="running",
                    used_fallback=False,
                    created_at=func.now(),
                    lease_expires_at=func.now() + func.make_interval(0, 0, 0, 0, 0, 0, float(lease_seconds)),
                    quota_period_start=period,
                )
                .on_conflict_do_nothing(constraint="uq_analyses_organization_id_idempotency_key")
                .returning(Analysis.id)
            )
        ).scalar_one_or_none()
        if claimed_id is not None:
            if quota is not None:
                await _consume_unit(
                    session, organization_id=organization_id, period=period, enforce=quota.enforce
                )
            # Same transaction as the claim and the unit: a refused quota leaves no analysis and no event.
            await insert_audit_event(session, AuditDraft(
                organization_id=organization_id,
                action="analysis.created",
                resource_id=str(claimed_id),
                source="user",
                actor_user_id=requested_by,
                details={"quota_period": None if period is None else f"{period.year:04d}-{period.month:02d}"},
            ))
            return ClaimResult("claimed", claimed_id, "running", None, None, None, False, None)

        analysis, expired = (
            await session.execute(
                select(Analysis, (Analysis.lease_expires_at <= func.now()).label("expired")).where(
                    Analysis.organization_id == organization_id,
                    Analysis.idempotency_key == idempotency_key,
                )
            )
        ).one()
        if analysis.input_hash != input_hash:
            outcome = "reused"
        elif analysis.status == "running" and expired:
            outcome = "expired"
        else:
            outcome = "existing"
        return ClaimResult(
            outcome,
            analysis.id,
            analysis.status,
            analysis.error_code,
            analysis.analysis_markdown,
            analysis.model_used,
            analysis.used_fallback,
            analysis.format_valid,
        )


async def _consume_unit(session: AsyncSession, *, organization_id: uuid.UUID, period: date, enforce: bool) -> None:
    """Takes one unit of the month, inside the claim's transaction.

    Enforced: one conditional UPDATE joined to the quota. It is the only statement that can take the last unit, and
    concurrent claims serialize on the usage row, so two claims never both pass with one unit left.
    Not enforced: counted unconditionally, so the history exists before a limit is switched on.
    """
    if not enforce:
        await session.execute(
            update(AnalysisQuotaUsage)
            .where(AnalysisQuotaUsage.organization_id == organization_id, AnalysisQuotaUsage.period_start == period)
            .values(consumed=AnalysisQuotaUsage.consumed + 1)
        )
        return
    configured = await session.scalar(
        select(AnalysisQuota.organization_id).where(AnalysisQuota.organization_id == organization_id)
    )
    if configured is None:
        raise QuotaNotConfiguredError
    result = await session.execute(
        update(AnalysisQuotaUsage)
        .where(
            AnalysisQuotaUsage.organization_id == organization_id,
            AnalysisQuotaUsage.period_start == period,
            AnalysisQuota.organization_id == organization_id,
            AnalysisQuotaUsage.consumed < AnalysisQuota.monthly_analysis_limit,
        )
        .values(consumed=AnalysisQuotaUsage.consumed + 1)
    )
    if result.rowcount != 1:
        raise QuotaExceededError


def _completion_time(created_at: datetime) -> ColumnElement[datetime]:
    """now(), but never before created_at.

    now() is transaction_timestamp(): the start of the current transaction. The system clock of the host can step
    backwards (measured: about 0.5 s, identically for now(), statement_timestamp() and clock_timestamp()). A row created
    in one transaction and completed in a later one could then carry completed_at < created_at, which the database
    CHECK refuses. GREATEST keeps the invariant without relying on the clock being monotonic. See
    docs/PHASE2_PROVIDER_USAGE.md, section 8.
    """
    return func.greatest(func.now(), created_at)


async def _release_unit_if_uncalled(
    session: AsyncSession, *, organization_id: uuid.UUID, analysis_id: uuid.UUID, period: date | None
) -> None:
    """Gives the unit back only when no provider call was ever reserved. Runs in the closing transaction.

    The caller holds the analysis row lock, so no call can be reserved between the count and the release.
    """
    if period is None:
        return
    calls = await session.scalar(
        select(func.count())
        .select_from(AnalysisUsage)
        .where(AnalysisUsage.organization_id == organization_id, AnalysisUsage.analysis_id == analysis_id)
    )
    if calls:
        return
    await session.execute(
        update(AnalysisQuotaUsage)
        .where(AnalysisQuotaUsage.organization_id == organization_id, AnalysisQuotaUsage.period_start == period)
        .values(consumed=AnalysisQuotaUsage.consumed - 1, released=AnalysisQuotaUsage.released + 1)
    )


async def _locked_analysis(session: AsyncSession, *, organization_id: uuid.UUID, analysis_id: uuid.UUID) -> Analysis:
    analysis = await session.scalar(
        select(Analysis)
        .where(Analysis.organization_id == organization_id, Analysis.id == analysis_id)
        .with_for_update()
    )
    if analysis is None:
        raise LookupError("analysis not found in this organization")
    return analysis


async def reserve_attempt(
    database: Database,
    *,
    organization_id: uuid.UUID,
    analysis_id: uuid.UUID,
    attempt_number: int,
    step: str,
    model_role: str,
    model: str,
) -> uuid.UUID:
    """Commits a 'reserved' row before the HTTP call. Refused by the database unless the lease is live."""
    async with database.session() as session:
        attempt = AnalysisUsage(
            organization_id=organization_id,
            analysis_id=analysis_id,
            attempt_number=attempt_number,
            step=step,
            model_role=model_role,
            model=model,
            status="reserved",
            usage_reported=False,
        )
        session.add(attempt)
        await session.flush()
        return attempt.id


async def complete_attempt(
    database: Database,
    *,
    organization_id: uuid.UUID,
    attempt_id: uuid.UUID,
    status: str,
    error_code: str | None = None,
    http_status: int | None = None,
    usage: ProviderUsage | None = None,
) -> None:
    """Records the outcome of a reserved call. 'unknown' means the outcome or its cost could not be confirmed."""
    async with database.session() as session:
        attempt = await session.get(AnalysisUsage, attempt_id)
        if attempt is None or attempt.organization_id != organization_id:
            raise LookupError("attempt not found in this organization")
        attempt.status = status
        attempt.error_code = error_code
        attempt.http_status = http_status
        attempt.completed_at = _completion_time(attempt.created_at)
        # Only values the provider reported are written. usage_reported means at least one of them was reported.
        if usage is not None and usage.has_any_value:
            attempt.usage_reported = True
            attempt.prompt_tokens = usage.prompt_tokens
            attempt.completion_tokens = usage.completion_tokens
            attempt.provider_cost = usage.provider_cost
            attempt.cost_micros = usage.cost_micros
        await session.flush()


async def finalize_analysis(
    database: Database,
    *,
    organization_id: uuid.UUID,
    analysis_id: uuid.UUID,
    status: str,
    error_code: str | None = None,
    analysis_markdown: str | None = None,
    model_used: str | None = None,
    used_fallback: bool = False,
    format_valid: bool | None = None,
    latency_ms: int | None = None,
) -> None:
    """Moves a running analysis to 'succeeded' or 'failed'. Refused unless the lease is live and no attempt is reserved.

    A 'failed' analysis that never reserved a call gives its quota unit back, in the same transaction.
    """
    async with database.session() as session:
        analysis = await _locked_analysis(session, organization_id=organization_id, analysis_id=analysis_id)
        period = analysis.quota_period_start
        analysis.status = status
        analysis.error_code = error_code
        analysis.analysis_markdown = analysis_markdown
        analysis.model_used = model_used
        analysis.used_fallback = used_fallback
        analysis.format_valid = format_valid
        analysis.latency_ms = latency_ms
        analysis.lease_expires_at = None
        analysis.completed_at = _completion_time(analysis.created_at)
        await session.flush()
        if status == "failed":
            await _release_unit_if_uncalled(
                session, organization_id=organization_id, analysis_id=analysis_id, period=period
            )
        # Written by the same transaction that changed the status: a refused write rolls back the event too.
        if status == "succeeded":
            await insert_audit_event(session, AuditDraft(
                organization_id=organization_id, action="analysis.succeeded", resource_id=str(analysis_id), source="internal",
                details={"model_used": model_used, "used_fallback": bool(used_fallback), "format_valid": bool(format_valid)},
            ))
        else:
            await insert_audit_event(session, AuditDraft(
                organization_id=organization_id, action="analysis.failed", resource_id=str(analysis_id), source="internal",
                details={"error_code": error_code, "used_fallback": bool(used_fallback)},
            ))


async def abandon_expired(database: Database, *, organization_id: uuid.UUID, analysis_id: uuid.UUID) -> bool:
    """Closes a running analysis whose lease expired. Its reserved calls become 'unknown'. Never makes a provider call.

    All writes share one transaction. The database refuses either update while the lease is still live. The unit is
    released only if no call was ever reserved for the analysis.
    """
    async with database.session() as session:
        analysis = await session.scalar(
            select(Analysis)
            .where(Analysis.organization_id == organization_id, Analysis.id == analysis_id)
            .with_for_update()
        )
        if analysis is None:
            return False
        period = analysis.quota_period_start
        await session.execute(
            update(AnalysisUsage)
            .where(
                AnalysisUsage.organization_id == organization_id,
                AnalysisUsage.analysis_id == analysis_id,
                AnalysisUsage.status == "reserved",
            )
            .values(status="unknown", error_code=ABANDONED, completed_at=func.greatest(func.now(), AnalysisUsage.created_at))
        )
        result = await session.execute(
            update(Analysis)
            .where(
                Analysis.organization_id == organization_id,
                Analysis.id == analysis_id,
                Analysis.status == "running",
                Analysis.lease_expires_at <= func.now(),
            )
            .values(
                status="failed",
                error_code=ABANDONED,
                completed_at=func.greatest(func.now(), Analysis.created_at),
                lease_expires_at=None,
                used_fallback=False,
            )
        )
        if result.rowcount != 1:
            return False
        await _release_unit_if_uncalled(
            session, organization_id=organization_id, analysis_id=analysis_id, period=period
        )
        await insert_audit_event(session, AuditDraft(
            organization_id=organization_id, action="analysis.failed", resource_id=str(analysis_id), source="internal",
            details={"error_code": ABANDONED, "used_fallback": False},
        ))
        return True
