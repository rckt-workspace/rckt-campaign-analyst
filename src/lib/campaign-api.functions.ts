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

const requestSchema = z.object({
  data: payloadSchema,
  /**
   * Optional organization chosen in the UI. It is a selection only: FastAPI validates it against the
   * user's stored memberships. When omitted, the first authorized organization is used.
   */
  organizationId: z.string().uuid().optional(),
});

export const analyzeCampaign = createServerFn({ method: "POST" })
  .inputValidator((input: unknown) => requestSchema.parse(input))
  .handler(async ({ data: request }) => {
    // Loaded here, not at module level, so server-only code never reaches the browser bundle.
    const { fastapiRequest, FastapiError } = await import("./fastapi-client.server");
    type Me = import("./fastapi-client.server").MeResponse;
    const { AuthNotConfiguredError, createSupabaseServerClient } = await import("./supabase/server");

    let supabase;
    try {
      supabase = createSupabaseServerClient();
    } catch (error) {
      if (error instanceof AuthNotConfiguredError) throw new Error(ANALYSIS_ERRORS.SESSION_EXPIRED);
      throw error;
    }

    // Identity is established from the signed session cookie, never from anything the browser sends.
    const { data: claimsData, error: claimsError } = await supabase.auth.getClaims();
    if (claimsError || !claimsData) throw new Error(ANALYSIS_ERRORS.SESSION_EXPIRED);

    const { data: sessionData } = await supabase.auth.getSession();
    const token = sessionData.session?.access_token;
    if (!token) throw new Error(ANALYSIS_ERRORS.SESSION_EXPIRED);

    try {
      let organizationId = request.organizationId;
      if (!organizationId) {
        const me = await fastapiRequest<Me>("/api/v1/me", { token, timeoutMs: 10_000 });
        const first = me.organizations[0];
        if (!first) throw new Error(ANALYSIS_ERRORS.NO_ORGANIZATION);
        organizationId = first.id;
      }

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
