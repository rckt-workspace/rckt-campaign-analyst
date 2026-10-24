"""Public API request/response schemas. Field names preserve the React contract."""

import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


MetricNumber = Annotated[float, Field(allow_inf_nan=False)]
NullableMetric = MetricNumber | None


class PreviousMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    investment: NullableMetric = None
    impressions: NullableMetric = None
    reach: NullableMetric = None
    frequency: NullableMetric = None
    clicks: NullableMetric = None
    ctr: NullableMetric = None
    cpc: NullableMetric = None
    leads: NullableMetric = None
    cpl: NullableMetric = None
    conversions: NullableMetric = None
    revenue: NullableMetric = None
    roas: NullableMetric = None


class CampaignInput(BaseModel):
    """Same field names and maximum lengths as the original Zod server function."""

    model_config = ConfigDict(extra="forbid")

    campaign: str | None = Field(default=None, max_length=2000)
    channel: str | None = Field(default=None, max_length=2000)
    objective: str | None = Field(default=None, max_length=2000)
    period: str | None = Field(default=None, max_length=2000)
    investment: NullableMetric = None
    impressions: NullableMetric = None
    reach: NullableMetric = None
    frequency: NullableMetric = None
    clicks: NullableMetric = None
    ctr: NullableMetric = None
    cpc: NullableMetric = None
    leads: NullableMetric = None
    cpl: NullableMetric = None
    conversions: NullableMetric = None
    revenue: NullableMetric = None
    roas: NullableMetric = None
    additionalContext: str | None = Field(default=None, max_length=4000)
    rawInput: str = Field(max_length=12000)
    previous: PreviousMetrics = Field(default_factory=PreviousMetrics)


class AnalysisResponse(BaseModel):
    analysis: str


class MeUser(BaseModel):
    """Minimal identity. The provider subject and other internal fields are deliberately not exposed."""

    id: uuid.UUID
    email: str | None


class MeOrganization(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    role: str


class MeResponse(BaseModel):
    user: MeUser
    organizations: list[MeOrganization]
