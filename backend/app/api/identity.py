"""Identity endpoint: who am I and which organizations am I authorized for."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db_session
from app.db.models import AppUser
from app.db.repositories import MembershipRepository
from app.domain.schemas import MeOrganization, MeResponse, MeUser

router = APIRouter(prefix="/api/v1", tags=["identity"])


@router.get("/me", response_model=MeResponse)
async def read_me(
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> MeResponse:
    rows = await MembershipRepository(session).list_for_user(user.id)
    return MeResponse(
        user=MeUser(id=user.id, email=user.email),
        organizations=[
            MeOrganization(id=organization.id, slug=organization.slug, name=organization.name, role=role)
            for organization, role in rows
        ],
    )
