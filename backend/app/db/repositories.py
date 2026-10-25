"""Data access. Every query that reads organization data takes organization_id as a required argument."""

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppUser, Organization, OrganizationMembership
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
