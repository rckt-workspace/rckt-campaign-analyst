"""Audit events of one organization. Owner only, read-only, paginated by id (newest first).

GET /api/v1/organizations/{organization_id}/audit-events?cursor=&limit=
- Authentication required. No membership: 404. Membership without the owner role: 403.
- cursor: the id of the last event of the previous page. limit: 1 to 100, default 50.
There is no route to modify or delete an event.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_database, get_db_session
from app.db.models import AppUser
from app.db.session import Database
from app.domain.access import ADMIN_ROLES
from app.domain.schemas import AuditEventPageRead, AuditEventRead
from app.services import audit as audit_service
from app.services.access import OrganizationNotFoundError, PermissionDeniedError, require_role

router = APIRouter(prefix="/api/v1/organizations/{organization_id}/audit-events", tags=["audit"])
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100


@router.get(
    "",
    response_model=AuditEventPageRead,
    responses={
        403: {"description": "Authenticated member without the owner role."},
        404: {"description": "No membership in this organization."},
        422: {"description": "Invalid cursor or limit."},
    },
)
async def list_audit_events(
    organization_id: uuid.UUID,
    cursor: int | None = Query(default=None, ge=1),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    database: Database = Depends(get_database),
) -> AuditEventPageRead:
    try:
        await require_role(session, organization_id=organization_id, user_id=user.id, allowed=ADMIN_ROLES)
    except OrganizationNotFoundError:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except PermissionDeniedError:
        raise HTTPException(status_code=403, detail="FORBIDDEN") from None
    rows, has_more = await audit_service.list_events(database, organization_id=organization_id, before=cursor, limit=limit)
    items = [
        AuditEventRead(
            id=row.id, occurred_at=row.occurred_at, source=row.source, actor_user_id=row.actor_user_id,
            action=row.action, resource_type=row.resource_type, resource_id=row.resource_id, metadata=row.details,
        )
        for row in rows
    ]
    return AuditEventPageRead(items=items, next_cursor=items[-1].id if has_more and items else None)
