"""Monthly usage report for one organization. Owner only, read-only.

GET /api/v1/organizations/{organization_id}/usage?period=YYYY-MM
- Authentication required. No membership: 404. Membership without the owner role: 403.
- period is a UTC month. Omitted: the current UTC month. Anything else: 422.
Permissions are checked in the backend, with the membership of the path's organization.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_database, get_db_session
from app.core.config import QuotaConfigurationError, analysis_quota_enforced
from app.db.clock import utc_quota_period
from app.db.models import AppUser
from app.db.session import Database, DatabaseUnavailableError
from app.domain.access import ADMIN_ROLES
from app.domain.audit import AuditDraft
from app.domain.schemas import UsageReportRead
from app.domain.usage_report import PERIOD_PATTERN, parse_period
from app.services import audit as audit_service
from app.services import usage_report as usage_service
from app.services.access import OrganizationNotFoundError, PermissionDeniedError, require_role

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/organizations/{organization_id}/usage", tags=["usage"])


@router.get(
    "",
    response_model=UsageReportRead,
    responses={
        403: {"description": "Authenticated member without the owner role, or organization not active."},
        404: {"description": "No membership in this organization."},
        422: {"description": "period is not YYYY-MM."},
        503: {"description": "ANALYSIS_QUOTA_ENFORCED has an invalid value, or the access could not be audited."},
    },
)
async def usage_report(
    organization_id: uuid.UUID,
    period: str | None = Query(default=None, pattern=PERIOD_PATTERN, description="UTC month, YYYY-MM."),
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    database: Database = Depends(get_database),
) -> UsageReportRead:
    try:
        await require_role(session, organization_id=organization_id, user_id=user.id, allowed=ADMIN_ROLES)
    except OrganizationNotFoundError:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except PermissionDeniedError:
        raise HTTPException(status_code=403, detail="FORBIDDEN") from None
    try:
        enforced = analysis_quota_enforced()
    except QuotaConfigurationError:
        logger.error("ANALYSIS_QUOTA_ENFORCED has an invalid value; usage report refused")
        raise HTTPException(status_code=503, detail="QUOTA_CONFIGURATION_INVALID") from None

    if period is not None:
        chosen = parse_period(period)
    else:
        # The default month is read from PostgreSQL, the same clock that assigns quota months (app/db/clock.py).
        month = await session.scalar(select(utc_quota_period(func.now())))
        chosen = parse_period(f"{month.year:04d}-{month.month:02d}")
    report = await usage_service.usage_report(
        database, organization_id=organization_id, period=chosen, quota_enforced=enforced
    )
    # Authorized and computed: the access is recorded before the report is delivered. If the record fails, the
    # report is not delivered, so nobody reads consumption that was not audited.
    try:
        await audit_service.record_event(database, AuditDraft(
            organization_id=organization_id,
            action="usage.report_accessed",
            resource_id=chosen.label,
            source="user",
            actor_user_id=user.id,
            details={"period": chosen.label},
        ))
    except (DBAPIError, DatabaseUnavailableError):
        logger.error("usage report access could not be audited; report not delivered")
        raise HTTPException(status_code=503, detail="AUDIT_UNAVAILABLE") from None
    return report
