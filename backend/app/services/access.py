"""Authorization for organization-scoped operations. Every organization-owned endpoint must call require_role."""

import uuid
from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import OrganizationMembership
from app.db.repositories import MembershipRepository
from app.domain.access import Role


class OrganizationNotFoundError(Exception):
    """Also raised when the organization exists but the user is not a member, so existence is not revealed."""


class PermissionDeniedError(Exception):
    """The user is a member, but their role does not allow the operation."""


async def require_role(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    allowed: Iterable[Role],
) -> OrganizationMembership:
    membership = await MembershipRepository(session).get_for_user(
        organization_id=organization_id, user_id=user_id
    )
    if membership is None:
        raise OrganizationNotFoundError
    if Role(membership.role) not in set(allowed):
        raise PermissionDeniedError
    return membership
