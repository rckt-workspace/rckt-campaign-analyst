"""Analysis endpoints under one organization: create (idempotent), history (paginated) and one report.

Transactions: the create route does not depend on get_db_session. That dependency would hold one transaction open for
the whole request, including the provider call. Identity and authorization run in short sessions instead.
"""

import logging
import re
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_database, get_db_session, get_verified_identity
from app.db.models import AppUser
from app.db.repositories import UserRepository
from app.db.session import Database
from app.core.config import QuotaConfigurationError, analysis_quota_enforced
from app.domain.access import PAID_AI_ROLES, VIEW_ROLES, Role
from app.domain.quota import QuotaExceededError, QuotaNotConfiguredError
from app.domain.schemas import (
    AnalysisAttemptRead,
    AnalysisAttemptUsageRead,
    AnalysisCreate,
    AnalysisDetailRead,
    AnalysisDetailUsageRead,
    AnalysisPageRead,
    AnalysisSummaryRead,
)
from app.security.tokens import VerifiedIdentity
from app.services import analysis_history
from app.services.access import OrganizationNotFoundError, PermissionDeniedError, require_role
from app.services.analysis_history import (
    AnalysisInProgressError,
    IdempotencyKeyReusedError,
    MetricEntryMismatchError,
    MetricEntryNotFoundError,
)
from app.services.campaigns import CampaignNotFoundError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/organizations/{organization_id}/analyses", tags=["analyses"])

IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
MAX_OFFSET = 100_000


async def _resolve_user(database: Database, identity: VerifiedIdentity) -> uuid.UUID:
    async with database.session() as session:
        user = await UserRepository(session).get_or_create_by_subject(
            external_subject=identity.subject, email=identity.email
        )
        return user.id


async def _authorize(database: Database, *, organization_id: uuid.UUID, user_id: uuid.UUID, allowed) -> Role:  # type: ignore[no-untyped-def]
    async with database.session() as session:
        try:
            membership = await require_role(session, organization_id=organization_id, user_id=user_id, allowed=allowed)
        except OrganizationNotFoundError:
            raise HTTPException(status_code=404, detail="NOT_FOUND") from None
        except PermissionDeniedError:
            raise HTTPException(status_code=403, detail="FORBIDDEN") from None
    return Role(membership.role)


def _detail(analysis, attempts, *, with_usage: bool) -> dict:  # type: ignore[no-untyped-def]
    """The report as JSON. Only the owner receives consumption: other roles get the operational fields, and the
    consumption keys are absent. Absent is not zero and not null: the response says nothing about it.
    """
    fields = dict(
        **analysis_history.summary_of(analysis),
        analysis_markdown=analysis.analysis_markdown,
        deterministic_report=analysis.deterministic_report,
    )
    if with_usage:
        detail = AnalysisDetailUsageRead(
            **fields,
            attempts=[AnalysisAttemptUsageRead.model_validate(attempt, from_attributes=True) for attempt in attempts],
        )
    else:
        detail = AnalysisDetailRead(
            **fields,
            attempts=[AnalysisAttemptRead.model_validate(attempt, from_attributes=True) for attempt in attempts],
        )
    return detail.model_dump(mode="json")


@router.post("", response_model=None)
async def create_analysis(
    organization_id: uuid.UUID,
    payload: AnalysisCreate,
    response: Response,
    identity: VerifiedIdentity = Depends(get_verified_identity),
    database: Database = Depends(get_database),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    if idempotency_key is None:
        raise HTTPException(status_code=400, detail="IDEMPOTENCY_KEY_REQUIRED")
    if not IDEMPOTENCY_KEY_PATTERN.match(idempotency_key):
        raise HTTPException(status_code=400, detail="IDEMPOTENCY_KEY_INVALID")
    try:
        quota_enforced = analysis_quota_enforced()
    except QuotaConfigurationError:
        raise HTTPException(status_code=503, detail="QUOTA_CONFIGURATION_INVALID") from None

    user_id = await _resolve_user(database, identity)
    role = await _authorize(database, organization_id=organization_id, user_id=user_id, allowed=PAID_AI_ROLES)
    try:
        result = await analysis_history.create_analysis(
            database,
            organization_id=organization_id,
            user_id=user_id,
            payload=payload,
            idempotency_key=idempotency_key,
            quota_enforced=quota_enforced,
        )
    except QuotaExceededError:
        raise HTTPException(status_code=429, detail="QUOTA_EXCEEDED") from None
    except QuotaNotConfiguredError:
        # Operator configuration, not a permission: 503 with no organization details in the answer.
        logger.error("analysis quota is not configured for the requested organization")
        raise HTTPException(status_code=503, detail="QUOTA_NOT_CONFIGURED") from None
    except IdempotencyKeyReusedError:
        raise HTTPException(status_code=422, detail="IDEMPOTENCY_KEY_REUSED") from None
    except AnalysisInProgressError:
        raise HTTPException(status_code=409, detail="ANALYSIS_IN_PROGRESS") from None
    except CampaignNotFoundError:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except MetricEntryNotFoundError:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except MetricEntryMismatchError:
        raise HTTPException(status_code=422, detail="ENTRY_DOES_NOT_MATCH") from None

    found = await analysis_history.get_analysis(
        database, organization_id=organization_id, analysis_id=result.analysis_id
    )
    if found is None:  # cannot happen after a successful claim; answered as a server error, not a silent success
        raise HTTPException(status_code=500, detail="INTERNAL_ERROR")
    analysis, attempts = found
    if result.replayed:
        response.status_code = 200
        response.headers["Idempotent-Replayed"] = "true"
    else:
        response.status_code = 201
    return _detail(analysis, attempts, with_usage=role is Role.OWNER)


@router.get("", response_model=AnalysisPageRead)
async def list_analyses(
    organization_id: uuid.UUID,
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0, le=MAX_OFFSET),
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    database: Database = Depends(get_database),
) -> AnalysisPageRead:
    try:
        await require_role(session, organization_id=organization_id, user_id=user.id, allowed=VIEW_ROLES)
    except OrganizationNotFoundError:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except PermissionDeniedError:
        raise HTTPException(status_code=403, detail="FORBIDDEN") from None
    items, has_more = await analysis_history.list_analyses(
        database, organization_id=organization_id, limit=limit, offset=offset
    )
    return AnalysisPageRead(
        items=[AnalysisSummaryRead(**item) for item in items], limit=limit, offset=offset, has_more=has_more
    )


@router.get("/{analysis_id}", response_model=None)
async def get_analysis(
    organization_id: uuid.UUID,
    analysis_id: uuid.UUID,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    database: Database = Depends(get_database),
) -> dict:
    try:
        membership = await require_role(session, organization_id=organization_id, user_id=user.id, allowed=VIEW_ROLES)
    except OrganizationNotFoundError:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except PermissionDeniedError:
        raise HTTPException(status_code=403, detail="FORBIDDEN") from None
    found = await analysis_history.get_analysis(database, organization_id=organization_id, analysis_id=analysis_id)
    if found is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    analysis, attempts = found
    return _detail(analysis, attempts, with_usage=Role(membership.role) is Role.OWNER)
