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

      const response = await fastapiRequest<{ analysis: string }>(
        `/api/v1/organizations/${organizationId}/campaigns/analyze`,
        { token, method: "POST", body: request.data, timeoutMs: 110_000 },
      );
      if (typeof response.analysis !== "string" || !response.analysis.trim()) {
        throw new Error(ANALYSIS_ERRORS.UNAVAILABLE);
      }
      return { analysis: response.analysis };
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

/** Keeps the decimals the database stores (numeric(14,2), numeric(14,4), ...). Rounding is representation, not loss. */
function roundTo(value: number | null, places: number): number | null {
  return value === null ? null : Number(value.toFixed(places));
}

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
    investment: roundTo(data.investment, 2),
    impressions: data.impressions,
    reach: data.reach,
    frequency: roundTo(data.frequency, 4),
    clicks: data.clicks,
    ctr: roundTo(data.ctr, 4),
    cpc: roundTo(data.cpc, 4),
    leads: data.leads,
    cpl: roundTo(data.cpl, 4),
    conversions: data.conversions,
    revenue: roundTo(data.revenue, 2),
    roas: roundTo(data.roas, 4),
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
 * Creates the campaign, then records its first metric entry. These are two requests, so a failure after the first
 * leaves a campaign without metrics. An atomic endpoint is pending (see docs/PHASE2_CAMPAIGNS.md).
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
      const base = `/api/v1/organizations/${organizationId}/campaigns`;

      const campaign = await fastapiRequest<CampaignRecord>(base, {
        token,
        method: "POST",
        body: campaignBody(request.data),
        timeoutMs: 10_000,
      });
      await fastapiRequest(`${base}/${campaign.id}/metrics`, {
        token,
        method: "POST",
        body: metricBody(request.data, request.source),
        timeoutMs: 10_000,
      });
      return { id: campaign.id };
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
