"""Campaign use cases. Callers check membership and role with require_role before calling these functions.

Rules enforced here:
- Archived campaigns are read-only: they accept no edits and no new metric entries. Their history stays readable.
- Metric entries are append-only. This module has no function that changes or removes one.
- Every lookup is scoped by organization_id, so a campaign of another organization is reported as not found.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppUser, Campaign, CampaignMetricEntry
from app.db.repositories import CampaignMetricRepository, CampaignRepository
from app.domain.campaigns import validate_period
from app.domain.schemas import CampaignCreate, CampaignMetricCreate, CampaignUpdate


class CampaignNotFoundError(Exception):
    """Also raised when the campaign exists in another organization, so existence is not revealed."""


class CampaignArchivedError(Exception):
    """The campaign is archived. Its history can be read, but it cannot change."""


async def _get_campaign(session: AsyncSession, *, organization_id: uuid.UUID, campaign_id: uuid.UUID) -> Campaign:
    campaign = await CampaignRepository(session).get(organization_id=organization_id, campaign_id=campaign_id)
    if campaign is None:
        raise CampaignNotFoundError
    return campaign


async def create_campaign(
    session: AsyncSession, *, organization_id: uuid.UUID, user: AppUser, payload: CampaignCreate
) -> Campaign:
    return await CampaignRepository(session).create(
        organization_id=organization_id, created_by=user.id, values=payload.model_dump()
    )


async def list_campaigns(
    session: AsyncSession, *, organization_id: uuid.UUID, include_archived: bool, limit: int, offset: int
) -> Sequence[Campaign]:
    return await CampaignRepository(session).list_for_organization(
        organization_id=organization_id, include_archived=include_archived, limit=limit, offset=offset
    )


async def get_campaign(session: AsyncSession, *, organization_id: uuid.UUID, campaign_id: uuid.UUID) -> Campaign:
    return await _get_campaign(session, organization_id=organization_id, campaign_id=campaign_id)


async def update_campaign(
    session: AsyncSession, *, organization_id: uuid.UUID, campaign_id: uuid.UUID, payload: CampaignUpdate
) -> Campaign:
    repository = CampaignRepository(session)
    campaign = await _get_campaign(session, organization_id=organization_id, campaign_id=campaign_id)
    if campaign.archived_at is not None:
        raise CampaignArchivedError

    # Only the fields the client sent change. The period is checked against the merged result, not the request alone.
    changes = payload.model_dump(include=payload.model_fields_set)
    validate_period(
        changes.get("period_start", campaign.period_start),
        changes.get("period_end", campaign.period_end),
    )
    for field, value in changes.items():
        setattr(campaign, field, value)
    return await repository.save(campaign)


async def archive_campaign(session: AsyncSession, *, organization_id: uuid.UUID, campaign_id: uuid.UUID) -> Campaign:
    """Idempotent: archiving an archived campaign returns it unchanged. Nothing is deleted."""
    repository = CampaignRepository(session)
    campaign = await _get_campaign(session, organization_id=organization_id, campaign_id=campaign_id)
    if campaign.archived_at is None:
        campaign.archived_at = datetime.now(UTC)
        campaign = await repository.save(campaign)
    return campaign


async def record_metric_entry(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    campaign_id: uuid.UUID,
    user: AppUser,
    payload: CampaignMetricCreate,
) -> CampaignMetricEntry:
    campaign = await _get_campaign(session, organization_id=organization_id, campaign_id=campaign_id)
    if campaign.archived_at is not None:
        raise CampaignArchivedError

    values = payload.model_dump(exclude={"previous"})
    values["previous_metrics"] = payload.previous.model_dump(mode="json")
    return await CampaignMetricRepository(session).add(
        organization_id=organization_id, campaign_id=campaign_id, created_by=user.id, values=values
    )


async def list_metric_entries(
    session: AsyncSession, *, organization_id: uuid.UUID, campaign_id: uuid.UUID, limit: int, offset: int
) -> Sequence[CampaignMetricEntry]:
    await _get_campaign(session, organization_id=organization_id, campaign_id=campaign_id)
    return await CampaignMetricRepository(session).list_for_campaign(
        organization_id=organization_id, campaign_id=campaign_id, limit=limit, offset=offset
    )
