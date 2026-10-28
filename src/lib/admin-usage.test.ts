import { test } from "node:test";
import assert from "node:assert/strict";
import {
  costState,
  currentPeriod,
  isPeriod,
  recentPeriods,
  tokenState,
  toUsageReport,
  type UsageReportRecord,
} from "./admin-usage";

function record(
  overrides: Partial<UsageReportRecord["provider_calls"]["cost"]> = {},
  tokens = {},
): UsageReportRecord {
  return {
    organization_id: "00000000-0000-4000-8000-0000000000a1",
    period: "2026-10",
    period_start: "2026-10-01",
    period_end_exclusive: "2026-11-01",
    timezone: "UTC",
    has_activity: true,
    analyses: { requested: 2, succeeded: 2, failed: 0, running: 0, without_quota_record: 0 },
    provider_calls: {
      total: 3,
      succeeded: 2,
      failed: 1,
      reserved: 0,
      unknown: 0,
      tokens: {
        prompt_total: 812,
        completion_total: 430,
        calls_with_prompt_reported: 1,
        calls_with_completion_reported: 1,
        ...tokens,
      },
      cost: {
        unit: "openrouter_credits",
        known_total: "0.00012345",
        calls_with_cost: 1,
        calls_without_cost: 2,
        explicit_zero_cost_calls: 0,
        completeness: "partial",
        ...overrides,
      },
    },
    quota: {
      configured: true,
      plan: "pilot",
      monthly_analysis_limit: 10,
      enforcement_enabled: false,
      analyses_counted: 2,
      units_consumed: 2,
      units_released: 0,
      reconciled: true,
      remaining_available: false,
      remaining: null,
    },
  };
}

test("the cost total stays the exact text the backend sent, never a Number", () => {
  const report = toUsageReport(record({ known_total: "0.10000002" }));
  assert.equal(report.cost.knownTotal, "0.10000002");
  assert.equal(typeof report.cost.knownTotal, "string");
});

test("an explicit zero is kept as zero and is different from an unknown total", () => {
  const explicitZero = toUsageReport(
    record({
      known_total: "0.00000000",
      explicit_zero_cost_calls: 1,
      calls_without_cost: 0,
      calls_with_cost: 3,
      completeness: "complete",
    }),
  );
  const unknown = toUsageReport(
    record({ known_total: null, calls_with_cost: 0, calls_without_cost: 3, completeness: "none" }),
  );

  assert.equal(explicitZero.cost.knownTotal, "0.00000000");
  assert.equal(explicitZero.cost.explicitZeroCostCalls, 1);
  assert.equal(unknown.cost.knownTotal, null);
  assert.equal(costState(explicitZero.cost), "known");
  assert.equal(costState(unknown.cost), "unknown");
});

test("a partial cost is marked partial, never complete", () => {
  const report = toUsageReport(record());
  assert.equal(report.cost.completeness, "partial");
  assert.equal(costState(report.cost), "partial");
});

test("missing token totals stay null and are shown as unknown", () => {
  const report = toUsageReport(
    record({}, { prompt_total: null, completion_total: null, calls_with_prompt_reported: 0 }),
  );
  assert.equal(report.calls.promptTokens, null);
  assert.equal(
    tokenState(report.calls.promptTokens, report.calls.callsWithPromptReported, report.calls.total),
    "unknown",
  );
  assert.equal(tokenState(812, 1, 3), "partial");
  assert.equal(tokenState(812, 3, 3), "known");
});

test("remaining units are exposed only when the backend declares them available", () => {
  const notAvailable = toUsageReport(record());
  assert.equal(notAvailable.quota.remaining, null);
  const withRemaining = record();
  withRemaining.quota = {
    ...withRemaining.quota,
    enforcement_enabled: true,
    remaining_available: true,
    remaining: 8,
  };
  assert.equal(toUsageReport(withRemaining).quota.remaining, 8);
});

test("an unknown completeness value is refused instead of guessed", () => {
  const broken = record();
  (broken.provider_calls.cost as { completeness: string }).completeness = "maybe";
  assert.throws(() => toUsageReport(broken), /USAGE_RESPONSE_INVALID/);
});

test("periods are UTC calendar months, newest first, across a year boundary", () => {
  const january = new Date("2027-01-01T00:30:00Z");
  assert.deepEqual(recentPeriods(january, 3), ["2027-01", "2026-12", "2026-11"]);
  assert.equal(currentPeriod(new Date("2026-10-31T23:59:59Z")), "2026-10");
  // 2026-11-01 04:30 UTC is November, whatever the viewer's zone is.
  assert.equal(currentPeriod(new Date("2026-11-01T04:30:00Z")), "2026-11");
});

test("only YYYY-MM periods within the backend range are accepted", () => {
  assert.equal(isPeriod("2026-10"), true);
  for (const raw of [
    "2026-13",
    "2026-00",
    "2026-1",
    "26-10",
    "1999-10",
    "2026-10-01",
    "",
    "octubre",
  ]) {
    assert.equal(isPeriod(raw), false, raw);
  }
});
