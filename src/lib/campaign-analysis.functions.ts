import { createServerFn } from "@tanstack/react-start";
import { z } from "zod";

const numeric = z.number().finite().nullable();
const text = z.string().max(2000).nullable();

const payloadSchema = z.object({
  campaign: text,
  channel: text,
  objective: text,
  period: text,
  investment: numeric,
  impressions: numeric,
  reach: numeric,
  frequency: numeric,
  clicks: numeric,
  ctr: numeric,
  cpc: numeric,
  leads: numeric,
  cpl: numeric,
  conversions: numeric,
  revenue: numeric,
  roas: numeric,
  additionalContext: z.string().max(4000).nullable(),
  rawInput: z.string().max(12000),
});

export const analyzeCampaign = createServerFn({ method: "POST" })
  .inputValidator((data: unknown) => payloadSchema.parse(data))
  .handler(async ({ data }) => {
    const { requestCampaignAnalysis } = await import("./campaign-analysis.server");
    const analysis = await requestCampaignAnalysis(data);
    if (!analysis) throw new Error("AI_EMPTY");
    return { analysis };
  });
