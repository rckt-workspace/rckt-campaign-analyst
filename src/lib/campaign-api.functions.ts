import { createServerFn } from "@tanstack/react-start";
import { z } from "zod";

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

export const analyzeCampaign = createServerFn({ method: "POST" })
  .inputValidator((data: unknown) => payloadSchema.parse(data))
  .handler(async ({ data }) => {
    const baseUrl = process.env["FASTAPI_INTERNAL_URL"] ?? "http://127.0.0.1:8000";
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 110_000);
    try {
      const response = await fetch(baseUrl.replace(/\/+$/, "") + "/api/v1/campaigns/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
        signal: controller.signal,
      });
      if (!response.ok) {
        console.error("Campaign API HTTP status:", response.status);
        throw new Error("CAMPAIGN_API_UNAVAILABLE");
      }
      const payload: unknown = await response.json();
      if (typeof payload !== "object" || payload === null ||
          !("analysis" in payload) || typeof payload.analysis !== "string" ||
          !payload.analysis.trim()) {
        throw new Error("CAMPAIGN_API_INVALID_RESPONSE");
      }
      return { analysis: payload.analysis };
    } catch (error) {
      console.error("Campaign API error", error instanceof Error ? error.message : "unknown");
      throw new Error("CAMPAIGN_API_UNAVAILABLE");
    } finally {
      clearTimeout(timeout);
    }
  });
