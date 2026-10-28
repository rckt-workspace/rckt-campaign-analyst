/**
 * Monthly usage report as the administrative screen reads it. Pure: no React, no network.
 *
 * Rules:
 * - Periods are UTC calendar months, YYYY-MM. The current month comes from the UTC clock.
 * - A missing value is null and is shown as unknown. It is never converted to zero.
 * - Credits are kept as the exact decimal text FastAPI sends. They are never parsed into Number, never converted to
 *   USD or COP, and never presented as an invoice.
 */

/** Same shape the backend accepts (domain/usage_report.py). */
export const PERIOD_PATTERN = /^(20[2-9][0-9])-(0[1-9]|1[0-2])$/;

export function isPeriod(value: string): boolean {
  return PERIOD_PATTERN.test(value);
}

/** The `count` UTC months ending at `now`, newest first. */
export function recentPeriods(now: Date, count: number): string[] {
  const periods: string[] = [];
  let year = now.getUTCFullYear();
  let month = now.getUTCMonth() + 1;
  for (let index = 0; index < count; index += 1) {
    periods.push(`${year}-${String(month).padStart(2, "0")}`);
    month -= 1;
    if (month === 0) {
      month = 12;
      year -= 1;
    }
  }
  return periods;
}

export function currentPeriod(now: Date): string {
  return recentPeriods(now, 1)[0] as string;
}

export type CostCompleteness = "no_calls" | "none" | "partial" | "complete";

/** FastAPI snake_case shape of UsageReportRead. Only the fields the screen reads are typed. */
export type UsageReportRecord = {
  organization_id: string;
  period: string;
  period_start: string;
  period_end_exclusive: string;
  timezone: string;
  has_activity: boolean;
  analyses: {
    requested: number;
    succeeded: number;
    failed: number;
    running: number;
    without_quota_record: number;
  };
  provider_calls: {
    total: number;
    succeeded: number;
    failed: number;
    reserved: number;
    unknown: number;
    tokens: {
      prompt_total: number | null;
      completion_total: number | null;
      calls_with_prompt_reported: number;
      calls_with_completion_reported: number;
    };
    cost: {
      unit: string;
      known_total: string | null;
      calls_with_cost: number;
      calls_without_cost: number;
      explicit_zero_cost_calls: number;
      completeness: CostCompleteness;
    };
  };
  quota: {
    configured: boolean;
    plan: string | null;
    monthly_analysis_limit: number | null;
    enforcement_enabled: boolean;
    analyses_counted: number;
    units_consumed: number;
    units_released: number;
    reconciled: boolean;
    remaining_available: boolean;
    remaining: number | null;
  };
};

export type UsageReport = {
  organizationId: string;
  period: string;
  periodStart: string;
  periodEndExclusive: string;
  hasActivity: boolean;
  analyses: {
    requested: number;
    succeeded: number;
    failed: number;
    running: number;
    withoutQuotaRecord: number;
  };
  calls: {
    total: number;
    succeeded: number;
    failed: number;
    reserved: number;
    unknown: number;
    promptTokens: number | null;
    completionTokens: number | null;
    callsWithPromptReported: number;
    callsWithCompletionReported: number;
  };
  cost: {
    unit: string;
    /** Exact decimal text, or null when no call reported a cost. */
    knownTotal: string | null;
    callsWithCost: number;
    callsWithoutCost: number;
    explicitZeroCostCalls: number;
    completeness: CostCompleteness;
  };
  quota: {
    configured: boolean;
    plan: string | null;
    monthlyAnalysisLimit: number | null;
    enforcementEnabled: boolean;
    analysesCounted: number;
    unitsConsumed: number;
    unitsReleased: number;
    reconciled: boolean;
    remainingAvailable: boolean;
    /** Only set when the backend declares remaining_available. Otherwise null, never a guess. */
    remaining: number | null;
  };
};

const COMPLETENESS: readonly CostCompleteness[] = ["no_calls", "none", "partial", "complete"];

export function toUsageReport(record: UsageReportRecord): UsageReport {
  if (!COMPLETENESS.includes(record.provider_calls.cost.completeness)) {
    throw new Error("USAGE_RESPONSE_INVALID");
  }
  return {
    organizationId: record.organization_id,
    period: record.period,
    periodStart: record.period_start,
    periodEndExclusive: record.period_end_exclusive,
    hasActivity: record.has_activity,
    analyses: {
      requested: record.analyses.requested,
      succeeded: record.analyses.succeeded,
      failed: record.analyses.failed,
      running: record.analyses.running,
      withoutQuotaRecord: record.analyses.without_quota_record,
    },
    calls: {
      total: record.provider_calls.total,
      succeeded: record.provider_calls.succeeded,
      failed: record.provider_calls.failed,
      reserved: record.provider_calls.reserved,
      unknown: record.provider_calls.unknown,
      promptTokens: record.provider_calls.tokens.prompt_total,
      completionTokens: record.provider_calls.tokens.completion_total,
      callsWithPromptReported: record.provider_calls.tokens.calls_with_prompt_reported,
      callsWithCompletionReported: record.provider_calls.tokens.calls_with_completion_reported,
    },
    cost: {
      unit: record.provider_calls.cost.unit,
      knownTotal: record.provider_calls.cost.known_total,
      callsWithCost: record.provider_calls.cost.calls_with_cost,
      callsWithoutCost: record.provider_calls.cost.calls_without_cost,
      explicitZeroCostCalls: record.provider_calls.cost.explicit_zero_cost_calls,
      completeness: record.provider_calls.cost.completeness,
    },
    quota: {
      configured: record.quota.configured,
      plan: record.quota.plan,
      monthlyAnalysisLimit: record.quota.monthly_analysis_limit,
      enforcementEnabled: record.quota.enforcement_enabled,
      analysesCounted: record.quota.analyses_counted,
      unitsConsumed: record.quota.units_consumed,
      unitsReleased: record.quota.units_released,
      reconciled: record.quota.reconciled,
      remainingAvailable: record.quota.remaining_available,
      remaining: record.quota.remaining_available ? record.quota.remaining : null,
    },
  };
}

/** How a value is shown: known, unknown (null), or partial (a known total that does not cover every call). */
export type ValueState = "known" | "unknown" | "partial";

export function tokenState(value: number | null, callsReported: number, calls: number): ValueState {
  if (value === null) return "unknown";
  return callsReported < calls ? "partial" : "known";
}

export function costState(report: UsageReport["cost"]): ValueState {
  if (report.knownTotal === null) return "unknown";
  return report.completeness === "complete" ? "known" : "partial";
}

export const COMPLETENESS_LABEL: Record<CostCompleteness, string> = {
  no_calls: "Sin llamadas en el periodo",
  none: "Ningún coste reportado",
  partial: "Coste parcial: el total no incluye todas las llamadas",
  complete: "Coste completo: todas las llamadas reportaron su coste",
};
