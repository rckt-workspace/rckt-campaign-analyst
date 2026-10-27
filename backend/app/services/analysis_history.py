"""Persisted analysis use case: idempotent creation, one recorded row per provider call, and history reads.

Order of one creation, and why:
1. The claim is committed to PostgreSQL before any provider call. Only the request that inserts the row may call.
2. Each HTTP call is reserved (committed) before it is sent and completed (committed) after it returns. No database
   transaction stays open while the provider is waiting.
3. The analysis is finalized (committed) with a safe state. A format failure after the allowed retry is stored as
   'failed' with AI_INVALID_RESPONSE.

A replay with the same key never calls the provider again: it returns what was stored, or the running state.
An expired lease is abandoned, never resumed (see app/db/analysis_ledger.py).
"""

import hashlib
import json
import logging
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.exc import DBAPIError

from app.core.config import AIConfigurationError, configured_model_names
from app.db import analysis_ledger as ledger
from app.db.models import Analysis, AnalysisUsage, Campaign, CampaignMetricEntry
from app.db.repositories import AnalysisRepository, CampaignMetricRepository, CampaignRepository
from app.db.session import Database
from app.domain.metrics import analyze_metrics
from app.domain.quota import quota_period_for
from app.domain.schemas import AnalysisCreate, CampaignInput
from app.integrations.openrouter import AIProviderError, CallObserver
from app.services.analysis import SYSTEM_PROMPT, is_parseable, request_campaign_analysis, sanitize
from app.services.campaigns import CampaignNotFoundError

logger = logging.getLogger(__name__)

# Four HTTP calls at the largest allowed CHAT_TIMEOUT_MS (120 s), plus a margin. A longer lease only delays abandonment.
LEASE_SECONDS = 4 * 120 + 30
PROMPT_VERSION = "campaign-analyst-" + hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:12]
INVALID_RESPONSE = "AI_INVALID_RESPONSE"


class IdempotencyKeyReusedError(Exception):
    """The key belongs to a request with different input. Refused; nothing is called."""


class MetricEntryNotFoundError(Exception):
    """The metric entry does not exist in this campaign and organization. Also used for other organizations."""


class MetricEntryMismatchError(Exception):
    """The saved metric entry does not hold the text and values sent for analysis. Nothing is claimed."""


class AnalysisInProgressError(Exception):
    """The key belongs to an analysis that is still running with a live lease. Retry later; nothing is called."""


@dataclass(frozen=True)
class CreateResult:
    analysis_id: uuid.UUID
    replayed: bool


def fingerprint(
    data: CampaignInput, campaign_id: uuid.UUID | None, metric_entry_id: uuid.UUID | None = None
) -> str:
    """SHA-256 of the canonical input. Two requests with one key must have the same fingerprint.

    metric_entry_id is added only when present, so fingerprints of requests without it are unchanged.
    """
    body = {
        "data": data.model_dump(mode="json"),
        "campaign_id": str(campaign_id) if campaign_id else None,
        "prompt_version": PROMPT_VERSION,
    }
    if metric_entry_id is not None:
        body["metric_entry_id"] = str(metric_entry_id)
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# Version of the metrics_snapshot structure. Bump it when the list of fields changes.
METRICS_SNAPSHOT_VERSION = 1
SNAPSHOT_METRICS = (
    "investment", "impressions", "reach", "frequency", "clicks", "ctr",
    "cpc", "leads", "cpl", "conversions", "revenue", "roas",
)


def metrics_snapshot(data: CampaignInput) -> dict:
    """The normalized metric values the engine received, as sent (not calculated).

    Deliberately partial: rawInput, additionalContext and the free-text campaign fields are never copied here. Only
    numeric metrics, current and previous, that the request carried. Absent values are omitted, not stored as null.
    """
    current = {key: getattr(data, key) for key in SNAPSHOT_METRICS if getattr(data, key) is not None}
    previous = {key: getattr(data.previous, key) for key in SNAPSHOT_METRICS if getattr(data.previous, key) is not None}
    return {"version": METRICS_SNAPSHOT_VERSION, "metrics": current, "previous": previous}


def deterministic_report(data: CampaignInput) -> dict:
    """Metrics computed in Python, stored with the claim. The analysis text is produced later, from the same input.

    Analyses saved before the snapshot existed have no "metrics_snapshot" key. They are never backfilled.
    """
    report = analyze_metrics(data)
    return {
        "input_summary": {
            "campaign": data.campaign,
            "channel": data.channel,
            "objective": data.objective,
            "period": data.period,
        },
        "calculated": report.calculated,
        "comparisons": report.comparisons,
        "warnings": report.warnings,
        "metrics_snapshot": metrics_snapshot(data),
    }


class _StepObserver:
    """Records one request step's HTTP calls. Implements app.integrations.openrouter.CallObserver."""

    def __init__(self, recorder: "AttemptRecorder", step: str) -> None:
        self._recorder = recorder
        self._step = step

    async def started(self, model_role: str, model: str) -> object:
        recorder = self._recorder
        recorder.attempts += 1
        attempt_id = await ledger.reserve_attempt(
            recorder.database,
            organization_id=recorder.organization_id,
            analysis_id=recorder.analysis_id,
            attempt_number=recorder.attempts,
            step=self._step,
            model_role=model_role,
            model=model,
        )
        return (attempt_id, model_role, model)

    async def finished(self, token: object, *, error_code: str | None, http_status: int | None) -> None:
        recorder = self._recorder
        attempt_id, model_role, model = token  # type: ignore[misc]
        if error_code is None:
            await ledger.complete_attempt(
                recorder.database, organization_id=recorder.organization_id, attempt_id=attempt_id, status="succeeded"
            )
            recorder.successes.append((model, model_role))
            recorder.last_error = None
            return
        await ledger.complete_attempt(
            recorder.database,
            organization_id=recorder.organization_id,
            attempt_id=attempt_id,
            status="failed",
            error_code=error_code,
            http_status=http_status,
        )
        recorder.last_error = error_code


class AttemptRecorder:
    """Persists every real provider call of one analysis. Satisfies app.services.analysis.StepRecorder."""

    def __init__(self, database: Database, organization_id: uuid.UUID, analysis_id: uuid.UUID) -> None:
        self.database = database
        self.organization_id = organization_id
        self.analysis_id = analysis_id
        self.attempts = 0
        self.successes: list[tuple[str, str]] = []  # (model, model_role) of each call that answered
        self.last_error: str | None = None

    def for_step(self, step: str) -> CallObserver:
        return _StepObserver(self, step)  # type: ignore[return-value]


async def _close_as(
    database: Database,
    *,
    organization_id: uuid.UUID,
    analysis_id: uuid.UUID,
    error_code: str,
) -> None:
    """Finalizes a running analysis as failed. If the database refuses (the lease expired), it is abandoned instead."""
    try:
        await ledger.finalize_analysis(
            database, organization_id=organization_id, analysis_id=analysis_id, status="failed", error_code=error_code
        )
    except DBAPIError:
        closed = await ledger.abandon_expired(database, organization_id=organization_id, analysis_id=analysis_id)
        if not closed:
            logger.error("analysis could not be closed; it stays running until its lease expires")
            raise


async def _run_provider_flow(
    database: Database, organization_id: uuid.UUID, analysis_id: uuid.UUID, data: CampaignInput
) -> None:
    recorder = AttemptRecorder(database, organization_id, analysis_id)
    started = time.monotonic()
    try:
        text = await request_campaign_analysis(data, recorder=recorder)
    except AIConfigurationError:
        await _close_as(database, organization_id=organization_id, analysis_id=analysis_id, error_code="AI_NOT_CONFIGURED")
        return
    except AIProviderError:
        await _close_as(
            database, organization_id=organization_id, analysis_id=analysis_id,
            error_code=recorder.last_error or "INTERNAL_ERROR",
        )
        return
    except DBAPIError:
        await _close_as(database, organization_id=organization_id, analysis_id=analysis_id, error_code="INTERNAL_ERROR")
        return
    except Exception:
        # Never leave a running analysis behind an unexpected error: close it as failed, then surface the bug.
        await _close_as(database, organization_id=organization_id, analysis_id=analysis_id, error_code="INTERNAL_ERROR")
        raise

    final = sanitize(text)
    if not is_parseable(final):
        await _close_as(database, organization_id=organization_id, analysis_id=analysis_id, error_code=INVALID_RESPONSE)
        return

    # When a format retry happened, the first answer was not parseable; the returned text is the retry's. So the last
    # call that answered is the one that produced the stored report.
    model_used, model_role = recorder.successes[-1]
    try:
        await ledger.finalize_analysis(
            database,
            organization_id=organization_id,
            analysis_id=analysis_id,
            status="succeeded",
            analysis_markdown=final,
            model_used=model_used,
            used_fallback=model_role == "fallback",
            format_valid=True,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    except DBAPIError:
        # The lease expired while the provider answered: the database refuses the success. Close as abandoned.
        await _close_as(database, organization_id=organization_id, analysis_id=analysis_id, error_code="ANALYSIS_ABANDONED")


# Same default the interface uses when a saved campaign has no name. Kept here to compare the stored name.
DEFAULT_CAMPAIGN_NAME = "Campaña sin nombre"


def _text_or_none(value: str | None) -> str | None:
    stripped = (value or "").strip()
    return stripped or None


def link_matches(entry: CampaignMetricEntry, campaign: Campaign, data: CampaignInput) -> bool:
    """What an explicit link certifies, and only that.

    Certified, because both sides are stored and compared:
    - the entry's raw text, numeric metrics and previous values (the metric source);
    - the campaign's name, channel, objective and period label, as the save step wrote them.

    Not certified, so refused rather than implied: additionalContext. It is not stored anywhere, so a link
    cannot prove it. A request that carries it is analysed without a link (see the interface).
    """
    if data.additionalContext is not None:
        return False
    expected_name = _text_or_none(data.campaign) or DEFAULT_CAMPAIGN_NAME
    if campaign.name != expected_name:
        return False
    if campaign.channel != _text_or_none(data.channel) or campaign.objective != _text_or_none(data.objective):
        return False
    if campaign.period_label != _text_or_none(data.period):
        return False
    return entry_text_and_metrics_match(entry, data)


def entry_text_and_metrics_match(entry: CampaignMetricEntry, data: CampaignInput) -> bool:
    if entry.raw_input != data.rawInput:
        return False
    if entry.previous_metrics != data.previous.model_dump(mode="json"):
        return False
    for key in SNAPSHOT_METRICS:
        stored, sent = getattr(entry, key), getattr(data, key)
        if (stored is None) != (sent is None):
            return False
        if stored is not None and float(stored) != float(sent):
            return False
    return True


async def create_analysis(
    database: Database,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: AnalysisCreate,
    idempotency_key: str,
    quota_enforced: bool,
) -> CreateResult:
    """Creates or replays one analysis. quota_enforced is passed explicitly: there is no default that could skip quotas.

    Raises QuotaExceededError or QuotaNotConfiguredError only when enforcement is on; nothing is claimed in that case.
    """
    campaign = None
    if payload.campaign_id is not None:
        async with database.session() as session:
            campaign = await CampaignRepository(session).get(
                organization_id=organization_id, campaign_id=payload.campaign_id
            )
        if campaign is None:
            raise CampaignNotFoundError
    if payload.metric_entry_id is not None:
        # The explicit link is verified, never assumed: nothing is claimed unless it certifies what is analysed.
        async with database.session() as session:
            entry = await CampaignMetricRepository(session).get(
                organization_id=organization_id, campaign_id=payload.campaign_id, entry_id=payload.metric_entry_id
            )
        if entry is None:
            raise MetricEntryNotFoundError
        if not link_matches(entry, campaign, payload.data):
            raise MetricEntryMismatchError

    primary_model, _ = configured_model_names()
    # Counted in the UTC month of the claim. A replay is never counted again (see ledger.claim_analysis).
    claim = await ledger.claim_analysis(
        database,
        organization_id=organization_id,
        requested_by=user_id,
        campaign_id=payload.campaign_id,
        metric_entry_id=payload.metric_entry_id,
        idempotency_key=idempotency_key,
        input_hash=fingerprint(payload.data, payload.campaign_id, payload.metric_entry_id),
        prompt_version=PROMPT_VERSION,
        model_requested=primary_model,
        deterministic_report=deterministic_report(payload.data),
        lease_seconds=LEASE_SECONDS,
        quota_period=quota_period_for(datetime.now(timezone.utc)),
        enforce_quota=quota_enforced,
    )

    if claim.outcome == "reused":
        raise IdempotencyKeyReusedError
    if claim.outcome == "expired":
        # The owner died. Close the analysis; the provider is never called again for this key.
        await ledger.abandon_expired(database, organization_id=organization_id, analysis_id=claim.analysis_id)
        return CreateResult(claim.analysis_id, replayed=True)
    if claim.outcome == "existing":
        if claim.status == "running":
            raise AnalysisInProgressError
        return CreateResult(claim.analysis_id, replayed=True)

    await _run_provider_flow(database, organization_id, claim.analysis_id, payload.data)
    return CreateResult(claim.analysis_id, replayed=False)


def campaign_name_of(analysis: Analysis) -> str | None:
    summary = (analysis.deterministic_report or {}).get("input_summary") or {}
    return summary.get("campaign")


def summary_of(analysis: Analysis) -> dict:
    return {
        "id": analysis.id,
        "status": analysis.status,
        "error_code": analysis.error_code,
        "campaign_id": analysis.campaign_id,
        "campaign_name": campaign_name_of(analysis),
        "created_at": analysis.created_at,
        "completed_at": analysis.completed_at,
        "model_used": analysis.model_used,
        "used_fallback": analysis.used_fallback,
        "format_valid": analysis.format_valid,
        "latency_ms": analysis.latency_ms,
    }


async def list_analyses(
    database: Database, *, organization_id: uuid.UUID, limit: int, offset: int
) -> tuple[list[dict], bool]:
    """One extra row is fetched to know whether another page exists, without a count query."""
    async with database.session() as session:
        rows: Sequence[Analysis] = await AnalysisRepository(session).list_for_organization(
            organization_id=organization_id, limit=limit + 1, offset=offset
        )
    has_more = len(rows) > limit
    return [summary_of(row) for row in rows[:limit]], has_more


async def get_analysis(
    database: Database, *, organization_id: uuid.UUID, analysis_id: uuid.UUID
) -> tuple[Analysis, Sequence[AnalysisUsage]] | None:
    async with database.session() as session:
        repository = AnalysisRepository(session)
        analysis = await repository.get(organization_id=organization_id, analysis_id=analysis_id)
        if analysis is None:
            return None
        attempts = await repository.attempts_for(organization_id=organization_id, analysis_id=analysis_id)
        return analysis, attempts
