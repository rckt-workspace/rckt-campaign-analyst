"""Internal REST API. Reachable only through the frontend server; every route here requires a verified identity."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db_session
from app.core.config import AIConfigurationError, QuotaConfigurationError, analysis_quota_enforced
from app.db.models import AppUser
from app.domain.access import PAID_AI_ROLES
from app.domain.schemas import AnalysisResponse, CampaignInput
from app.integrations.openrouter import AIProviderError
from app.services.access import OrganizationNotFoundError, PermissionDeniedError, require_role
from app.services.analysis import request_campaign_analysis

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["campaigns"])


@router.post("/organizations/{organization_id}/campaigns/analyze", response_model=AnalysisResponse)
async def analyze_campaign(
    organization_id: uuid.UUID,
    payload: CampaignInput,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> AnalysisResponse:
    # This route never claims or counts an analysis. Once quotas are enforced it is retired, so it cannot bypass them.
    try:
        enforced = analysis_quota_enforced()
    except QuotaConfigurationError:
        raise HTTPException(status_code=503, detail="QUOTA_CONFIGURATION_INVALID") from None
    if enforced:
        raise HTTPException(status_code=410, detail="ENDPOINT_RETIRED")

    # Authorization runs before any provider call, so refused requests never cost tokens.
    try:
        await require_role(session, organization_id=organization_id, user_id=user.id, allowed=PAID_AI_ROLES)
    except OrganizationNotFoundError:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except PermissionDeniedError:
        raise HTTPException(status_code=403, detail="FORBIDDEN") from None

    try:
        text = await request_campaign_analysis(payload)
    except AIConfigurationError:
        logger.error("AI server environment is incomplete")
        raise HTTPException(status_code=503, detail="AI backend unavailable") from None
    except AIProviderError:
        logger.error("AI upstream failed")
        raise HTTPException(status_code=502, detail="AI provider unavailable") from None
    return AnalysisResponse(analysis=text)
