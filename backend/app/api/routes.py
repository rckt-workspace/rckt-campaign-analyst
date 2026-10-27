"""Legacy analysis endpoint, retired. It answers 410 and never calls the provider or touches the database.

Replaced by POST /api/v1/organizations/{organization_id}/analyses, which persists, is idempotent and counts quota.
No consumer remains: the frontend moved to the persisted endpoint in Phase 2.5B (checked again in Phase 2.6A). The
route stays, answering 410, so old clients get a clear answer instead of a 404.
"""

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/v1", tags=["campaigns"])


@router.post("/organizations/{organization_id}/campaigns/analyze")
async def analyze_campaign_retired(organization_id: str) -> None:
    raise HTTPException(status_code=410, detail="ENDPOINT_RETIRED")
