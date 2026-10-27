"""Public API request/response schemas. Field names preserve the React contract."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator, model_validator

from app.domain.campaigns import validate_period


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


# --- Campaigns and metric entries (Phase 2.4) -------------------------------------------------------------
# Limits mirror the PostgreSQL column types: numeric(p, s) and bigint. A value the column cannot hold is
# rejected here as a 422, never as a database error.

BIGINT_MAX = 9_223_372_036_854_775_807
CURRENCY_PATTERN = r"^[A-Z]{3}$"


def _as_json_number(value: Decimal) -> float:
    # Stored exactly; returned as a JSON number, the same shape as the analysis contract the frontend uses.
    return float(value)


JsonDecimal = Annotated[Decimal, PlainSerializer(_as_json_number, return_type=float, when_used="json")]
# Money is never sent as a float: the exact decimal text travels, so the client reads what the provider reported.
ExactDecimal = Annotated[Decimal, PlainSerializer(lambda value: format(value, "f"), return_type=str, when_used="json")]
Amount = Annotated[JsonDecimal, Field(ge=0, max_digits=14, decimal_places=2)]  # numeric(14,2)
UnitCost = Annotated[JsonDecimal, Field(ge=0, max_digits=14, decimal_places=4)]  # numeric(14,4)
Ratio = Annotated[JsonDecimal, Field(ge=0, max_digits=12, decimal_places=4)]  # numeric(12,4)
Percentage = Annotated[JsonDecimal, Field(ge=0, max_digits=9, decimal_places=4)]  # numeric(9,4)
Count = Annotated[int, Field(ge=0, le=BIGINT_MAX)]  # bigint: fractional counts are rejected


class CampaignCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=2000)
    channel: str | None = Field(default=None, max_length=2000)
    objective: str | None = Field(default=None, max_length=2000)
    period_label: str | None = Field(default=None, max_length=2000)
    currency: str = Field(default="COP", pattern=CURRENCY_PATTERN)
    period_start: date | None = None
    period_end: date | None = None

    @field_validator("name")
    @classmethod
    def name_is_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped

    @model_validator(mode="after")
    def period_is_ordered(self) -> "CampaignCreate":
        validate_period(self.period_start, self.period_end)
        return self


class CampaignUpdate(BaseModel):
    """Partial update. Only fields sent in the request change. Null clears an optional field."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=2000)
    channel: str | None = Field(default=None, max_length=2000)
    objective: str | None = Field(default=None, max_length=2000)
    period_label: str | None = Field(default=None, max_length=2000)
    currency: str | None = Field(default=None, pattern=CURRENCY_PATTERN)
    period_start: date | None = None
    period_end: date | None = None

    @field_validator("name")
    @classmethod
    def name_is_not_blank(cls, value: str | None) -> str:
        # Runs only when the field is sent. The column is NOT NULL, so an explicit null is refused.
        if value is None:
            raise ValueError("name must not be null")
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped

    @field_validator("currency")
    @classmethod
    def currency_is_not_null(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("currency must not be null")
        return value


class CampaignRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    channel: str | None
    objective: str | None
    period_label: str | None
    currency: str
    period_start: date | None
    period_end: date | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CampaignMetricValues(BaseModel):
    """Metric values as entered. Shared by the request and the stored record. All values are optional."""

    period_label: str | None = Field(default=None, max_length=2000)
    period_start: date | None = None
    period_end: date | None = None
    investment: Amount | None = None
    impressions: Count | None = None
    reach: Count | None = None
    frequency: Ratio | None = None
    clicks: Count | None = None
    ctr: Percentage | None = None
    cpc: UnitCost | None = None
    leads: Count | None = None
    cpl: UnitCost | None = None
    conversions: Count | None = None
    revenue: Amount | None = None
    roas: Ratio | None = None

    @model_validator(mode="after")
    def period_is_ordered(self) -> "CampaignMetricValues":
        validate_period(self.period_start, self.period_end)
        return self


class CampaignMetricCreate(CampaignMetricValues):
    model_config = ConfigDict(extra="forbid")

    source: Literal["form", "text"] = "form"
    raw_input: str = Field(max_length=12000)
    previous: PreviousMetrics = Field(default_factory=PreviousMetrics)


class CampaignMetricRead(CampaignMetricValues):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    campaign_id: uuid.UUID
    source: str
    raw_input: str
    previous_metrics: PreviousMetrics
    created_at: datetime


class CampaignWithMetricsCreate(BaseModel):
    """A campaign and its first metric entry. Both are written in one transaction, or neither is."""

    model_config = ConfigDict(extra="forbid")

    campaign: CampaignCreate
    metrics: CampaignMetricCreate


class CampaignWithMetricsRead(BaseModel):
    campaign: CampaignRead
    metric_entry: CampaignMetricRead


# --- Analyses (Phase 2.5B) -------------------------------------------------------------------------------------

class AnalysisCreate(BaseModel):
    """Same campaign payload the analysis endpoint already accepts, plus an optional saved campaign.

    The organization comes from the path only. The Idempotency-Key header identifies the request.
    """

    model_config = ConfigDict(extra="forbid")

    campaign_id: uuid.UUID | None = None
    metric_entry_id: uuid.UUID | None = None
    data: CampaignInput

    @model_validator(mode="after")
    def entry_needs_campaign(self) -> "AnalysisCreate":
        if self.metric_entry_id is not None and self.campaign_id is None:
            raise ValueError("metric_entry_id requires campaign_id")
        return self


class AnalysisAttemptRead(BaseModel):
    """One HTTP call to the provider, in order. Operational fields only: every role that reads a report gets these."""

    attempt_number: int
    step: str
    model_role: str
    model: str
    status: str
    error_code: str | None
    http_status: int | None
    created_at: datetime
    completed_at: datetime | None


class AnalysisAttemptUsageRead(AnalysisAttemptRead):
    """Owner only: what the provider reported for this call. Null means not reported, never zero."""

    usage_reported: bool
    prompt_tokens: int | None
    completion_tokens: int | None
    cost_micros: int | None
    provider_cost: ExactDecimal | None


class AnalysisSummaryRead(BaseModel):
    """List item. Never contains the report text."""

    id: uuid.UUID
    status: str
    error_code: str | None
    campaign_id: uuid.UUID | None
    campaign_name: str | None
    created_at: datetime
    completed_at: datetime | None
    model_used: str | None
    used_fallback: bool
    format_valid: bool | None
    latency_ms: int | None


class AnalysisDetailRead(AnalysisSummaryRead):
    analysis_markdown: str | None
    deterministic_report: dict
    attempts: list[AnalysisAttemptRead]


class AnalysisDetailUsageRead(AnalysisDetailRead):
    """Owner only. Same report, with the consumption of each call."""

    attempts: list[AnalysisAttemptUsageRead]


class AnalysisPageRead(BaseModel):
    items: list[AnalysisSummaryRead]
    limit: int
    offset: int
    has_more: bool
