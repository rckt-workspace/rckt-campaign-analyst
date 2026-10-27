"""Storage of audit events. Writing always happens inside the caller's session, so the event commits or rolls back with the
operation it describes. Reading is paginated by id, newest first.

Nothing here updates or deletes: the database refuses both (migration 0008).
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEvent
from app.domain.audit import ACTION_RESOURCE_TYPE, AuditDraft


async def insert_audit_event(session: AsyncSession, draft: AuditDraft) -> int:
    """Adds the event to the caller's transaction and returns its id. The caller commits or rolls back."""
    row = AuditEvent(
        organization_id=draft.organization_id,
        source=draft.source,
        actor_user_id=draft.actor_user_id,
        action=draft.action,
        resource_type=ACTION_RESOURCE_TYPE[draft.action],
        resource_id=draft.resource_id,
        details=dict(draft.details),
    )
    session.add(row)
    await session.flush()
    return row.id


async def list_audit_events(
    session: AsyncSession, *, organization_id: uuid.UUID, before: int | None, limit: int
) -> tuple[list[AuditEvent], bool]:
    """One page, newest first. `before` is the id of the last event of the previous page: the next page starts below it.

    One extra row is fetched to know whether another page exists without a count.
    """
    statement = select(AuditEvent).where(AuditEvent.organization_id == organization_id)
    if before is not None:
        statement = statement.where(AuditEvent.id < before)
    rows = list((await session.scalars(statement.order_by(AuditEvent.id.desc()).limit(limit + 1))).all())
    return rows[:limit], len(rows) > limit
