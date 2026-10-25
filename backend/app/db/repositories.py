"""Data access. Every query that reads organization data takes organization_id as a required argument."""

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppUser, Campaign, CampaignMetricEntry, Organization, OrganizationMembership
from app.domain.access import Role


class OrganizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, name: str, slug: str) -> Organization:
        organization = Organization(name=name, slug=slug)
        self._session.add(organization)
        await self._session.flush()
        return organization

    async def get(self, organization_id: uuid.UUID) -> Organization | None:
        return await self._session.get(Organization, organization_id)

    async def list_for_user(self, user_id: uuid.UUID) -> Sequence[Organization]:
        statement = (
            select(Organization)
            .join(OrganizationMembership, OrganizationMembership.organization_id == Organization.id)
            .where(OrganizationMembership.user_id == user_id)
            .order_by(Organization.name)
        )
        return (await self._session.scalars(statement)).all()


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, external_subject: str, email: str | None = None) -> AppUser:
        user = AppUser(external_subject=external_subject, email=email)
        self._session.add(user)
        await self._session.flush()
        return user

    async def get_by_subject(self, external_subject: str) -> AppUser | None:
        return await self._session.scalar(select(AppUser).where(AppUser.external_subject == external_subject))

    async def get_by_email(self, email: str) -> AppUser | None:
        return await self._session.scalar(select(AppUser).where(AppUser.email == email.strip().lower()))

    async def get_or_create_by_subject(self, *, external_subject: str, email: str | None) -> AppUser:
        """Creates only the local identity row. A new user receives no membership and no role."""
        user = await self.get_by_subject(external_subject)
        if user is None:
            return await self.create(external_subject=external_subject, email=email)
        if email and user.email != email:
            user.email = email
            await self._session.flush()
        return user


class MembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, *, organization_id: uuid.UUID, user_id: uuid.UUID, role: Role) -> OrganizationMembership:
        membership = OrganizationMembership(organization_id=organization_id, user_id=user_id, role=role.value)
        self._session.add(membership)
        await self._session.flush()
        return membership

    async def list_for_user(self, user_id: uuid.UUID) -> Sequence[tuple[Organization, str]]:
        statement = (
            select(Organization, OrganizationMembership.role)
            .join(OrganizationMembership, OrganizationMembership.organization_id == Organization.id)
            .where(OrganizationMembership.user_id == user_id)
            .order_by(Organization.name)
        )
        return (await self._session.execute(statement)).all()

    async def get_for_user(
        self, *, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> OrganizationMembership | None:
        return await self._session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.user_id == user_id,
            )
        )


class CampaignRepository:
    """Campaign rows. Every method is scoped by organization_id; a campaign id from another organization is not found."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, organization_id: uuid.UUID, created_by: uuid.UUID, values: dict) -> Campaign:
        campaign = Campaign(organization_id=organization_id, created_by=created_by, **values)
        self._session.add(campaign)
        await self._session.flush()
        # Server-side defaults (created_at, updated_at) are not loaded by the flush; read them back before returning.
        await self._session.refresh(campaign)
        return campaign

    async def get(self, *, organization_id: uuid.UUID, campaign_id: uuid.UUID) -> Campaign | None:
        return await self._session.scalar(
            select(Campaign).where(Campaign.organization_id == organization_id, Campaign.id == campaign_id)
        )

    async def list_for_organization(
        self, *, organization_id: uuid.UUID, include_archived: bool, limit: int, offset: int
    ) -> Sequence[Campaign]:
        statement = select(Campaign).where(Campaign.organization_id == organization_id)
        if not include_archived:
            statement = statement.where(Campaign.archived_at.is_(None))
        statement = statement.order_by(Campaign.created_at.desc(), Campaign.id.desc()).limit(limit).offset(offset)
        return (await self._session.scalars(statement)).all()

    async def save(self, campaign: Campaign) -> Campaign:
        """Flushes changes to a campaign already loaded through get(), then reloads the database-maintained timestamps."""
        await self._session.flush()
        await self._session.refresh(campaign)
        return campaign


class CampaignMetricRepository:
    """Append-only. There is deliberately no update or delete method. PostgreSQL also rejects both (trigger)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self, *, organization_id: uuid.UUID, campaign_id: uuid.UUID, created_by: uuid.UUID, values: dict
    ) -> CampaignMetricEntry:
        entry = CampaignMetricEntry(
            organization_id=organization_id, campaign_id=campaign_id, created_by=created_by, **values
        )
        self._session.add(entry)
        await self._session.flush()
        await self._session.refresh(entry)
        return entry

    async def list_for_campaign(
        self, *, organization_id: uuid.UUID, campaign_id: uuid.UUID, limit: int, offset: int
    ) -> Sequence[CampaignMetricEntry]:
        statement = (
            select(CampaignMetricEntry)
            .where(
                CampaignMetricEntry.organization_id == organization_id,
                CampaignMetricEntry.campaign_id == campaign_id,
            )
            .order_by(CampaignMetricEntry.created_at.desc(), CampaignMetricEntry.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return (await self._session.scalars(statement)).all()
