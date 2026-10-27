"""Audit use cases: recording one event in its own transaction, and reading pages of events.

Recording inside an operation (claim, finalize, quota, membership) uses app.db.audit.insert_audit_event with the caller's
session. record_event is only for the one event that has no write of its own: a read that must be audited.
"""

import uuid

from app.db.audit import insert_audit_event, list_audit_events
from app.db.models import AuditEvent
from app.db.session import Database
from app.domain.audit import AuditDraft


async def record_event(database: Database, draft: AuditDraft) -> int:
    """Commits one event. Raises if the database refuses it: the caller must not deliver the result then."""
    async with database.session() as session:
        return await insert_audit_event(session, draft)


async def list_events(
    database: Database, *, organization_id: uuid.UUID, before: int | None, limit: int
) -> tuple[list[AuditEvent], bool]:
    async with database.session() as session:
        return await list_audit_events(session, organization_id=organization_id, before=before, limit=limit)
