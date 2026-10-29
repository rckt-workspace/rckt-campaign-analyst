"""Storage of audit events. Writing always happens inside the caller's session, so the event commits or rolls back with the
operation it describes. Reading is paginated by id, newest first.

Nothing here updates or deletes: the database refuses both (migration 0008).
"""

import json
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEvent
from app.domain.audit import ACTION_RESOURCE_TYPE, AuditDraft


async def insert_audit_event(session: AsyncSession, draft: AuditDraft) -> None:
    """Adds the event to the caller's transaction. The caller commits or rolls back.

    No RETURNING. The application role may append events but may not read them back: only the owner reads the trail
    (migration 0009). PostgreSQL requires a row returned by RETURNING to be visible to the inserting role, so an ORM
    insert that returns the id would be refused for every role but the owner. Nothing needs the id.
    """
    # Explicit INSERT with typed parameters. SQLAlchemy adds RETURNING for the identity column to every insert of this
    # table (ORM and Core alike), and RETURNING would make the row visible to the inserting role: see the docstring.
    await session.execute(
        text(
            "INSERT INTO public.audit_events"
            " (organization_id, source, actor_user_id, action, resource_type, resource_id, metadata)"
            " VALUES (CAST(:organization_id AS uuid), :source, CAST(:actor_user_id AS uuid), :action,"
            " :resource_type, :resource_id, CAST(:metadata AS jsonb))"
        ),
        {
            "organization_id": str(draft.organization_id),
            "source": draft.source,
            "actor_user_id": None if draft.actor_user_id is None else str(draft.actor_user_id),
            "action": draft.action,
            "resource_type": ACTION_RESOURCE_TYPE[draft.action],
            "resource_id": draft.resource_id,
            "metadata": json.dumps(dict(draft.details)),
        },
    )



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
