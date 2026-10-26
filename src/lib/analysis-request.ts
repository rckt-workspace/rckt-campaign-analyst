/**
 * What one analysis request sends, and how a retry repeats it. Pure: no React, no network.
 *
 * Rules:
 * - A request is built once, when the user asks for an analysis. It holds the input, the idempotency key and the
 *   link (if any). A retry sends the same object, so the body and the fingerprint on the server stay the same.
 * - The link (campaign id and metric entry id) travels only when the analysed input is exactly the input that was
 *   saved. The server verifies it again; this check only avoids sending a link that is known to be wrong.
 * - A new click creates a new request with a new key. A retry never adds a link that the first request did not have.
 * - When the server has stored a failure for a key, the retry needs a new key: the stored failure would be replayed.
 */
import type { NormalizedCampaign } from "./campaign-metrics";

export type SavedLink = {
  /** JSON of the input that was saved. The link is valid only for exactly this input. */
  key: string;
  campaignId: string;
  metricEntryId: string;
};

export type AnalysisRequest = {
  data: NormalizedCampaign;
  idempotencyKey: string;
  campaignId?: string;
  metricEntryId?: string;
};

export function inputKey(data: NormalizedCampaign): string {
  return JSON.stringify(data);
}

/** The link for this input, or no link. Never a link for different input. */
export function linkFor(
  data: NormalizedCampaign,
  saved: SavedLink | null,
): { campaignId?: string; metricEntryId?: string } {
  // additionalContext cannot be certified by a link (it is not stored), so such input is analysed without one.
  if (data.additionalContext !== null) return {};
  if (!saved || saved.key !== inputKey(data)) return {};
  return { campaignId: saved.campaignId, metricEntryId: saved.metricEntryId };
}

/** A new analysis: fresh key, and the link only if the input is the saved one. */
export function newRequest(
  data: NormalizedCampaign,
  saved: SavedLink | null,
  newKey: () => string,
): AnalysisRequest {
  return { data, idempotencyKey: newKey(), ...linkFor(data, saved) };
}

/** The same request with a fresh key, after the server stored a failure for the previous key. Body and link stay. */
export function withFreshKey(previous: AnalysisRequest, newKey: () => string): AnalysisRequest {
  return { ...previous, idempotencyKey: newKey() };
}
