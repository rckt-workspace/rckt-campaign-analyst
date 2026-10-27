/** Stable error codes shared by server functions and the UI. Contains no server-only imports. */
export const ANALYSIS_ERRORS = {
  SESSION_EXPIRED: "SESSION_EXPIRED",
  FORBIDDEN: "FORBIDDEN",
  NO_ORGANIZATION: "NO_ORGANIZATION",
  UNAVAILABLE: "CAMPAIGN_API_UNAVAILABLE",
  CAMPAIGN_INVALID: "CAMPAIGN_INVALID",
  CAMPAIGN_SAVE_FAILED: "CAMPAIGN_SAVE_FAILED",
  /** The server stored a failed analysis. A retry needs a new key: the stored failure would just be replayed. */
  ANALYSIS_FAILED: "ANALYSIS_FAILED",
  /** The organization has no analyses left this UTC month. Nothing was claimed or called. */
  QUOTA_EXCEEDED: "QUOTA_EXCEEDED",
} as const;

export type AnalysisErrorCode = (typeof ANALYSIS_ERRORS)[keyof typeof ANALYSIS_ERRORS];
