import { createServerFn } from "@tanstack/react-start";
import { z } from "zod";
import { ANALYSIS_ERRORS } from "./analysis-errors";

const numeric = z.number().finite().nullable();
const text = z.string().max(2000).nullable();
const payloadSchema = z.object({
  campaign: text, channel: text, objective: text, period: text,
  investment: numeric, impressions: numeric, reach: numeric, frequency: numeric,
  clicks: numeric, ctr: numeric, cpc: numeric, leads: numeric, cpl: numeric,
  conversions: numeric, revenue: numeric, roas: numeric,
  additionalContext: z.string().max(4000).nullable(),
  rawInput: z.string().max(12000),
  previous: z.object({
    investment: z.number().finite().optional(),
    impressions: z.number().finite().optional(),
    reach: z.number().finite().optional(),
    frequency: z.number().finite().optional(),
    clicks: z.number().finite().optional(),
    ctr: z.number().finite().optional(),
    cpc: z.number().finite().optional(),
    leads: z.number().finite().optional(),
    cpl: z.number().finite().optional(),
    conversions: z.number().finite().optional(),
    revenue: z.number().finite().optional(),
    roas: z.number().finite().optional(),
  }).default({}),
});

type CampaignPayload = z.infer<typeof payloadSchema>;

const requestSchema = z.object({
  data: payloadSchema,
  /**
   * Sent as the Idempotency-Key header. The same key on a retry returns the stored result and never starts a second
   * paid call. The browser creates it per analysis and reuses it when retrying that analysis.
   */
  idempotencyKey: z.string().min(8).max(128),
  /** Saved campaign and its entry, only when the analyzed input is exactly the saved one. The server verifies it. */
  campaignId: z.string().uuid().optional(),
  metricEntryId: z.string().uuid().optional(),
  /**
   * Optional organization chosen in the UI. It is a selection only: FastAPI validates it against the
   * user's stored memberships. When omitted, the first authorized organization is used.
   */
  organizationId: z.string().uuid().optional(),
});

const saveRequestSchema = z.object({
  data: payloadSchema,
  /** How the data was entered. Stored with the metric entry; the analysis itself does not depend on it. */
  source: z.enum(["form", "text"]),
  organizationId: z.string().uuid().optional(),
});

const listRequestSchema = z.object({
  organizationId: z.string().uuid().optional(),
});

/** FastAPI response for a campaign (snake_case), as returned by the campaign endpoints. */
type CampaignRecord = {
  id: string;
  name: string;
  channel: string | null;
  objective: string | null;
  period_label: string | null;
  created_at: string;
};

/** FastAPI analysis record: only the fields the report needs. */
type AnalysisRecord = { id: string; status: string; analysis_markdown: string | null };

/** FastAPI response of the atomic create endpoint. Only the campaign id is used by the UI. */
type CampaignWithMetricsRecord = { campaign: CampaignRecord; metric_entry: { id: string } };

/** Campaign as the UI uses it. Contains only what the list shows. */
export type SavedCampaign = {
  id: string;
  name: string;
  channel: string | null;
  objective: string | null;
  periodLabel: string | null;
  createdAt: string;
};

export const analyzeCampaign = createServerFn({ method: "POST" })
  .inputValidator((input: unknown) => requestSchema.parse(input))
  .handler(async ({ data: request }) => {
    // Loaded here, not at module level, so server-only code never reaches the browser bundle.
    const { fastapiRequest, FastapiError } = await import("./fastapi-client.server");
    const { currentAccessToken, resolveOrganizationId } = await import("./campaign-session.server");

    // Identity is established from the signed session cookie, never from anything the browser sends.
    const token = await currentAccessToken();

    try {
      const organizationId = await resolveOrganizationId(token, request.organizationId);

      // Persisted analysis: the answer is stored whatever its outcome, and a repeated key returns that stored result.
      const record = await fastapiRequest<AnalysisRecord>(
        `/api/v1/organizations/${organizationId}/analyses`,
        {
          token,
          method: "POST",
          body: {
            data: request.data,
            ...(request.campaignId ? { campaign_id: request.campaignId } : {}),
            ...(request.metricEntryId ? { metric_entry_id: request.metricEntryId } : {}),
          },
          headers: { "Idempotency-Key": request.idempotencyKey },
          timeoutMs: 200_000,
        },
      );
      if (record.status === "failed") throw new Error(ANALYSIS_ERRORS.ANALYSIS_FAILED);
      if (record.status !== "succeeded" || !record.analysis_markdown?.trim()) {
        throw new Error(ANALYSIS_ERRORS.UNAVAILABLE);
      }
      return { analysis: record.analysis_markdown, analysisId: record.id };
    } catch (error) {
      if (error instanceof Error && (Object.values(ANALYSIS_ERRORS) as string[]).includes(error.message)) throw error;
      if (error instanceof FastapiError) {
        if (error.status === 401) throw new Error(ANALYSIS_ERRORS.SESSION_EXPIRED);
        if (error.status === 403) throw new Error(ANALYSIS_ERRORS.FORBIDDEN);
        if (error.status === 404) throw new Error(ANALYSIS_ERRORS.NO_ORGANIZATION);
      }
      console.error("Campaign analysis unavailable");
      throw new Error(ANALYSIS_ERRORS.UNAVAILABLE);
    }
  });

/**
 * Values are sent exactly as the user entered them. They are not rounded here: FastAPI validates the precision of
 * each column and refuses a value it would have to round (422 CAMPAIGN_INVALID). A silent rounding of financial
 * data would store a number the user never wrote.
 */
function campaignBody(data: CampaignPayload) {
  return {
    name: data.campaign?.trim() || "Campaña sin nombre",
    channel: data.channel?.trim() || null,
    objective: data.objective?.trim() || null,
    period_label: data.period?.trim() || null,
  };
}

function metricBody(data: CampaignPayload, source: "form" | "text") {
  return {
    source,
    raw_input: data.rawInput,
    period_label: data.period?.trim() || null,
    investment: data.investment,
    impressions: data.impressions,
    reach: data.reach,
    frequency: data.frequency,
    clicks: data.clicks,
    ctr: data.ctr,
    cpc: data.cpc,
    leads: data.leads,
    cpl: data.cpl,
    conversions: data.conversions,
    revenue: data.revenue,
    roas: data.roas,
    previous: data.previous,
  };
}

function toSavedCampaign(record: CampaignRecord): SavedCampaign {
  return {
    id: record.id,
    name: record.name,
    channel: record.channel,
    objective: record.objective,
    periodLabel: record.period_label,
    createdAt: record.created_at,
  };
}

/**
 * One request. FastAPI creates the campaign and its first metric entry in a single transaction: either both are
 * stored or neither is.
 */
export const saveCampaign = createServerFn({ method: "POST" })
  .inputValidator((input: unknown) => saveRequestSchema.parse(input))
  .handler(async ({ data: request }) => {
    const { fastapiRequest } = await import("./fastapi-client.server");
    const { currentAccessToken, resolveOrganizationId, campaignErrorCode } =
      await import("./campaign-session.server");

    const token = await currentAccessToken();

    try {
      const organizationId = await resolveOrganizationId(token, request.organizationId);
      const result = await fastapiRequest<CampaignWithMetricsRecord>(
        `/api/v1/organizations/${organizationId}/campaigns/with-metrics`,
        {
          token,
          method: "POST",
          body: {
            campaign: campaignBody(request.data),
            metrics: metricBody(request.data, request.source),
          },
          timeoutMs: 10_000,
        },
      );
      return { id: result.campaign.id, metricEntryId: result.metric_entry.id };
    } catch (error) {
      console.error("Campaign save failed");
      throw new Error(campaignErrorCode(error, ANALYSIS_ERRORS.CAMPAIGN_SAVE_FAILED));
    }
  });

export const listSavedCampaigns = createServerFn({ method: "GET" })
  .inputValidator((input: unknown) => listRequestSchema.parse(input ?? {}))
  .handler(async ({ data: request }) => {
    const { fastapiRequest } = await import("./fastapi-client.server");
    const { currentAccessToken, resolveOrganizationId, campaignErrorCode } =
      await import("./campaign-session.server");

    const token = await currentAccessToken();

    try {
      const organizationId = await resolveOrganizationId(token, request.organizationId);
      const records = await fastapiRequest<CampaignRecord[]>(
        `/api/v1/organizations/${organizationId}/campaigns`,
        { token, timeoutMs: 10_000 },
      );
      return { campaigns: records.map(toSavedCampaign) };
    } catch (error) {
      console.error("Campaign list unavailable");
      throw new Error(campaignErrorCode(error, ANALYSIS_ERRORS.UNAVAILABLE));
    }
  });
