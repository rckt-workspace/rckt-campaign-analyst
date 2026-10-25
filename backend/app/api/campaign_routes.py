"""Campaign and metric-entry endpoints, scoped to one organization from the path.

Role matrix (docs/PHASE2_DESIGN.md, section 4, as refined for Phase 2.4):
- read campaigns and metric entries: owner, analyst, client_viewer
- create campaigns and append metric entries: owner, analyst
- edit and archive campaigns: owner only
There is no endpoint that changes or deletes a metric entry, and none that deletes a campaign.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db_session
from app.db.models import AppUser
from app.domain.access import CAMPAIGN_ADMIN_ROLES, RECORD_CAMPAIGN_ROLES, VIEW_ROLES, Role
from app.domain.campaigns import InvalidPeriodError
from app.domain.schemas import (
    CampaignCreate,
    CampaignMetricCreate,
    CampaignMetricRead,
    CampaignRead,
    CampaignUpdate,
    CampaignWithMetricsCreate,
    CampaignWithMetricsRead,
)
from app.services import campaigns as campaign_service
from app.services.access import OrganizationNotFoundError, PermissionDeniedError, require_role
from app.services.campaigns import CampaignArchivedError, CampaignNotFoundError

router = APIRouter(prefix="/api/v1/organizations/{organization_id}/campaigns", tags=["campaigns"])

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
MAX_OFFSET = 100_000


async def _authorize(
    session: AsyncSession, *, organization_id: uuid.UUID, user: AppUser, allowed: frozenset[Role]
) -> None:
    try:
        await require_role(session, organization_id=organization_id, user_id=user.id, allowed=allowed)
    except OrganizationNotFoundError:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except PermissionDeniedError:
        raise HTTPException(status_code=403, detail="FORBIDDEN") from None


@contextmanager
def _service_errors() -> Iterator[None]:
    """Maps domain outcomes to stable error codes. Messages are fixed strings, never database text."""
    try:
        yield
    except CampaignNotFoundError:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except CampaignArchivedError:
        raise HTTPException(status_code=409, detail="CAMPAIGN_ARCHIVED") from None
    except InvalidPeriodError:
        raise HTTPException(status_code=422, detail="INVALID_PERIOD") from None


@router.post("", status_code=201, response_model=CampaignRead)
async def create_campaign(
    organization_id: uuid.UUID,
    payload: CampaignCreate,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> CampaignRead:
    await _authorize(session, organization_id=organization_id, user=user, allowed=RECORD_CAMPAIGN_ROLES)
    campaign = await campaign_service.create_campaign(
        session, organization_id=organization_id, user=user, payload=payload
    )
    return CampaignRead.model_validate(campaign)


@router.post("/with-metrics", status_code=201, response_model=CampaignWithMetricsRead)
async def create_campaign_with_metrics(
    organization_id: uuid.UUID,
    payload: CampaignWithMetricsCreate,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> CampaignWithMetricsRead:
    """One request, one transaction: the campaign and its first metric entry, or nothing."""
    await _authorize(session, organization_id=organization_id, user=user, allowed=RECORD_CAMPAIGN_ROLES)
    with _service_errors():
        campaign, entry = await campaign_service.create_campaign_with_metrics(
            session, organization_id=organization_id, user=user, payload=payload
        )
    return CampaignWithMetricsRead(
        campaign=CampaignRead.model_validate(campaign),
        metric_entry=CampaignMetricRead.model_validate(entry),
    )


@router.get("", response_model=list[CampaignRead])
async def list_campaigns(
    organization_id: uuid.UUID,
    include_archived: bool = False,
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0, le=MAX_OFFSET),
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[CampaignRead]:
    await _authorize(session, organization_id=organization_id, user=user, allowed=VIEW_ROLES)
    campaigns = await campaign_service.list_campaigns(
        session, organization_id=organization_id, include_archived=include_archived, limit=limit, offset=offset
    )
    return [CampaignRead.model_validate(campaign) for campaign in campaigns]


@router.get("/{campaign_id}", response_model=CampaignRead)
async def get_campaign(
    organization_id: uuid.UUID,
    campaign_id: uuid.UUID,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> CampaignRead:
    await _authorize(session, organization_id=organization_id, user=user, allowed=VIEW_ROLES)
    with _service_errors():
        campaign = await campaign_service.get_campaign(
            session, organization_id=organization_id, campaign_id=campaign_id
        )
    return CampaignRead.model_validate(campaign)


@router.patch("/{campaign_id}", response_model=CampaignRead)
async def update_campaign(
    organization_id: uuid.UUID,
    campaign_id: uuid.UUID,
    payload: CampaignUpdate,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> CampaignRead:
    await _authorize(session, organization_id=organization_id, user=user, allowed=CAMPAIGN_ADMIN_ROLES)
    with _service_errors():
        campaign = await campaign_service.update_campaign(
            session, organization_id=organization_id, campaign_id=campaign_id, payload=payload
        )
    return CampaignRead.model_validate(campaign)


@router.post("/{campaign_id}/archive", response_model=CampaignRead)
async def archive_campaign(
    organization_id: uuid.UUID,
    campaign_id: uuid.UUID,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> CampaignRead:
    await _authorize(session, organization_id=organization_id, user=user, allowed=CAMPAIGN_ADMIN_ROLES)
    with _service_errors():
        campaign = await campaign_service.archive_campaign(
            session, organization_id=organization_id, campaign_id=campaign_id
        )
    return CampaignRead.model_validate(campaign)


@router.post("/{campaign_id}/metrics", status_code=201, response_model=CampaignMetricRead)
async def record_metric_entry(
    organization_id: uuid.UUID,
    campaign_id: uuid.UUID,
    payload: CampaignMetricCreate,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> CampaignMetricRead:
    await _authorize(session, organization_id=organization_id, user=user, allowed=RECORD_CAMPAIGN_ROLES)
    with _service_errors():
        entry = await campaign_service.record_metric_entry(
            session, organization_id=organization_id, campaign_id=campaign_id, user=user, payload=payload
        )
    return CampaignMetricRead.model_validate(entry)


@router.get("/{campaign_id}/metrics", response_model=list[CampaignMetricRead])
async def list_metric_entries(
    organization_id: uuid.UUID,
    campaign_id: uuid.UUID,
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0, le=MAX_OFFSET),
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[CampaignMetricRead]:
    await _authorize(session, organization_id=organization_id, user=user, allowed=VIEW_ROLES)
    with _service_errors():
        entries = await campaign_service.list_metric_entries(
            session, organization_id=organization_id, campaign_id=campaign_id, limit=limit, offset=offset
        )
    return [CampaignMetricRead.model_validate(entry) for entry in entries]
