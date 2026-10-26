"""Write protocol for analyses. Each function is one short transaction, and none stays open during a provider call.

Order for one request:
1. claim_analysis: commits the 'running' row (deterministic report, input hash, lease) before any provider call.
2. For each HTTP call: reserve_attempt commits, then the provider is called with no transaction open, then
   complete_attempt commits the outcome.
3. finalize_analysis commits the final state.

A lease that expired is never resumed with a new call: abandon_expired marks the reserved attempts 'unknown' and the
analysis 'failed' with ANALYSIS_ABANDONED. The database enforces the rules (triggers in migration 0004); these
functions only sequence the writes and report what the database accepted or refused.

Guarantees and limits are documented in docs/PHASE2_ANALYSES.md, section 4.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import Analysis, AnalysisUsage
from app.db.session import Database
from app.domain.analysis import ABANDONED


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
class ProviderUsage:
    """Usage the provider actually reported for one call. Absent fields stay NULL; nothing is estimated."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_micros: int | None = None


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
) -> ClaimResult:
    """Inserts the running analysis, or finds the one that owns the key. Concurrent claims: exactly one inserts."""
    async with database.session() as session:
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
                    lease_expires_at=func.now() + func.make_interval(0, 0, 0, 0, 0, 0, float(lease_seconds)),
                )
                .on_conflict_do_nothing(constraint="uq_analyses_organization_id_idempotency_key")
                .returning(Analysis.id)
            )
        ).scalar_one_or_none()
        if claimed_id is not None:
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
        attempt.completed_at = func.now()
        if usage is not None:
            attempt.usage_reported = True
            attempt.prompt_tokens = usage.prompt_tokens
            attempt.completion_tokens = usage.completion_tokens
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
    """Moves a running analysis to 'succeeded' or 'failed'. Refused unless the lease is live and no attempt is reserved."""
    async with database.session() as session:
        analysis = await session.get(Analysis, analysis_id)
        if analysis is None or analysis.organization_id != organization_id:
            raise LookupError("analysis not found in this organization")
        analysis.status = status
        analysis.error_code = error_code
        analysis.analysis_markdown = analysis_markdown
        analysis.model_used = model_used
        analysis.used_fallback = used_fallback
        analysis.format_valid = format_valid
        analysis.latency_ms = latency_ms
        analysis.lease_expires_at = None
        analysis.completed_at = func.now()
        await session.flush()


async def abandon_expired(database: Database, *, organization_id: uuid.UUID, analysis_id: uuid.UUID) -> bool:
    """Closes a running analysis whose lease expired. Its reserved calls become 'unknown'. Never makes a provider call.

    Both updates share one transaction. The database refuses either one while the lease is still live.
    """
    async with database.session() as session:
        await session.execute(
            update(AnalysisUsage)
            .where(
                AnalysisUsage.organization_id == organization_id,
                AnalysisUsage.analysis_id == analysis_id,
                AnalysisUsage.status == "reserved",
            )
            .values(status="unknown", error_code=ABANDONED, completed_at=func.now())
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
                completed_at=func.now(),
                lease_expires_at=None,
                used_fallback=False,
            )
        )
        return result.rowcount == 1
