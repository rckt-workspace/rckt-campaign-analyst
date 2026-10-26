import { createServerFn } from "@tanstack/react-start";
import { z } from "zod";
import { ANALYSIS_ERRORS } from "./analysis-errors";

/** Saved analysis as the history list shows it. Never contains the report text. */
export type AnalysisSummary = {
  id: string;
  status: "running" | "succeeded" | "failed";
  errorCode: string | null;
  campaignName: string | null;
  createdAt: string;
  completedAt: string | null;
  modelUsed: string | null;
  usedFallback: boolean;
  formatValid: boolean | null;
  latencyMs: number | null;
};

/** One saved analysis with its report text, for reopening a previous report. */
export type AnalysisDetail = AnalysisSummary & { analysisMarkdown: string | null };

export type AnalysisPage = {
  items: AnalysisSummary[];
  hasMore: boolean;
  offset: number;
  limit: number;
};

/** FastAPI shapes (snake_case), as returned by the analysis endpoints. */
type SummaryRecord = {
  id: string;
  status: AnalysisSummary["status"];
  error_code: string | null;
  campaign_name: string | null;
  created_at: string;
  completed_at: string | null;
  model_used: string | null;
  used_fallback: boolean;
  format_valid: boolean | null;
  latency_ms: number | null;
};

type DetailRecord = SummaryRecord & { analysis_markdown: string | null };

type PageRecord = { items: SummaryRecord[]; has_more: boolean; offset: number; limit: number };

const listSchema = z.object({
  organizationId: z.string().uuid().optional(),
  limit: z.number().int().min(1).max(100).default(20),
  offset: z.number().int().min(0).max(100_000).default(0),
});

const detailSchema = z.object({
  organizationId: z.string().uuid().optional(),
  analysisId: z.string().uuid(),
});

function toSummary(record: SummaryRecord): AnalysisSummary {
  return {
    id: record.id,
    status: record.status,
    errorCode: record.error_code,
    campaignName: record.campaign_name,
    createdAt: record.created_at,
    completedAt: record.completed_at,
    modelUsed: record.model_used,
    usedFallback: record.used_fallback,
    formatValid: record.format_valid,
    latencyMs: record.latency_ms,
  };
}

/** Maps a failed FastAPI call to a stable UI code. Upstream bodies never reach the browser. */
function historyErrorCode(error: unknown): string {
  if (
    error instanceof Error &&
    (Object.values(ANALYSIS_ERRORS) as string[]).includes(error.message)
  ) {
    return error.message;
  }
  const status = (error as { status?: unknown } | null)?.status;
  if (status === 401) return ANALYSIS_ERRORS.SESSION_EXPIRED;
  if (status === 403) return ANALYSIS_ERRORS.FORBIDDEN;
  if (status === 404) return ANALYSIS_ERRORS.NO_ORGANIZATION;
  return ANALYSIS_ERRORS.UNAVAILABLE;
}

export const listAnalyses = createServerFn({ method: "GET" })
  .inputValidator((input: unknown) => listSchema.parse(input ?? {}))
  .handler(async ({ data: request }): Promise<AnalysisPage> => {
    const { fastapiRequest } = await import("./fastapi-client.server");
    const { currentAccessToken, resolveOrganizationId } = await import("./campaign-session.server");

    const token = await currentAccessToken();
    try {
      const organizationId = await resolveOrganizationId(token, request.organizationId);
      const page = await fastapiRequest<PageRecord>(
        `/api/v1/organizations/${organizationId}/analyses?limit=${request.limit}&offset=${request.offset}`,
        { token, timeoutMs: 10_000 },
      );
      return {
        items: page.items.map(toSummary),
        hasMore: page.has_more,
        offset: page.offset,
        limit: page.limit,
      };
    } catch (error) {
      console.error("Analysis history unavailable");
      throw new Error(historyErrorCode(error));
    }
  });

export const getAnalysis = createServerFn({ method: "GET" })
  .inputValidator((input: unknown) => detailSchema.parse(input))
  .handler(async ({ data: request }): Promise<AnalysisDetail> => {
    const { fastapiRequest } = await import("./fastapi-client.server");
    const { currentAccessToken, resolveOrganizationId } = await import("./campaign-session.server");

    const token = await currentAccessToken();
    try {
      const organizationId = await resolveOrganizationId(token, request.organizationId);
      const record = await fastapiRequest<DetailRecord>(
        `/api/v1/organizations/${organizationId}/analyses/${request.analysisId}`,
        { token, timeoutMs: 10_000 },
      );
      return { ...toSummary(record), analysisMarkdown: record.analysis_markdown };
    } catch (error) {
      console.error("Analysis detail unavailable");
      throw new Error(historyErrorCode(error));
    }
  });
