"""Internal REST API. It is reachable only by the frontend container."""

import logging

from fastapi import APIRouter, HTTPException

from app.core.config import AIConfigurationError
from app.domain.schemas import AnalysisResponse, CampaignInput
from app.integrations.openrouter import AIProviderError
from app.services.analysis import request_campaign_analysis

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["campaigns"])


@router.post("/campaigns/analyze", response_model=AnalysisResponse)
async def analyze_campaign(payload: CampaignInput) -> AnalysisResponse:
    try:
        text = await request_campaign_analysis(payload)
    except AIConfigurationError:
        logger.error("AI server environment is incomplete")
        raise HTTPException(status_code=503, detail="AI backend unavailable") from None
    except AIProviderError:
        logger.error("AI upstream failed")
        raise HTTPException(status_code=502, detail="AI provider unavailable") from None
    return AnalysisResponse(analysis=text)
