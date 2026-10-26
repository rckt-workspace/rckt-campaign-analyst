"""Analysis endpoints under one organization: create (idempotent), history (paginated) and one report.

Transactions: the create route does not depend on get_db_session. That dependency would hold one transaction open for
the whole request, including the provider call. Identity and authorization run in short sessions instead.
"""

import re
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_database, get_db_session, get_verified_identity
from app.db.models import AppUser
from app.db.repositories import UserRepository
from app.db.session import Database
from app.domain.access import PAID_AI_ROLES, VIEW_ROLES
from app.domain.schemas import (
    AnalysisAttemptRead,
    AnalysisCreate,
    AnalysisDetailRead,
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


async def _authorize(database: Database, *, organization_id: uuid.UUID, user_id: uuid.UUID, allowed) -> None:  # type: ignore[no-untyped-def]
    async with database.session() as session:
        try:
            await require_role(session, organization_id=organization_id, user_id=user_id, allowed=allowed)
        except OrganizationNotFoundError:
            raise HTTPException(status_code=404, detail="NOT_FOUND") from None
        except PermissionDeniedError:
            raise HTTPException(status_code=403, detail="FORBIDDEN") from None


def _detail(analysis, attempts) -> AnalysisDetailRead:  # type: ignore[no-untyped-def]
    summary = analysis_history.summary_of(analysis)
    return AnalysisDetailRead(
        **summary,
        analysis_markdown=analysis.analysis_markdown,
        deterministic_report=analysis.deterministic_report,
        attempts=[AnalysisAttemptRead.model_validate(attempt, from_attributes=True) for attempt in attempts],
    )


@router.post("", response_model=AnalysisDetailRead)
async def create_analysis(
    organization_id: uuid.UUID,
    payload: AnalysisCreate,
    response: Response,
    identity: VerifiedIdentity = Depends(get_verified_identity),
    database: Database = Depends(get_database),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AnalysisDetailRead:
    if idempotency_key is None:
        raise HTTPException(status_code=400, detail="IDEMPOTENCY_KEY_REQUIRED")
    if not IDEMPOTENCY_KEY_PATTERN.match(idempotency_key):
        raise HTTPException(status_code=400, detail="IDEMPOTENCY_KEY_INVALID")

    user_id = await _resolve_user(database, identity)
    await _authorize(database, organization_id=organization_id, user_id=user_id, allowed=PAID_AI_ROLES)
    try:
        result = await analysis_history.create_analysis(
            database,
            organization_id=organization_id,
            user_id=user_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )
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
    return _detail(analysis, attempts)


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


@router.get("/{analysis_id}", response_model=AnalysisDetailRead)
async def get_analysis(
    organization_id: uuid.UUID,
    analysis_id: uuid.UUID,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    database: Database = Depends(get_database),
) -> AnalysisDetailRead:
    try:
        await require_role(session, organization_id=organization_id, user_id=user.id, allowed=VIEW_ROLES)
    except OrganizationNotFoundError:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except PermissionDeniedError:
        raise HTTPException(status_code=403, detail="FORBIDDEN") from None
    found = await analysis_history.get_analysis(database, organization_id=organization_id, analysis_id=analysis_id)
    if found is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    analysis, attempts = found
    return _detail(analysis, attempts)
