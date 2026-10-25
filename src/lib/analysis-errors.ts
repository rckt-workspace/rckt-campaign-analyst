/** Stable error codes shared by server functions and the UI. Contains no server-only imports. */
export const ANALYSIS_ERRORS = {
  SESSION_EXPIRED: "SESSION_EXPIRED",
  FORBIDDEN: "FORBIDDEN",
  NO_ORGANIZATION: "NO_ORGANIZATION",
  UNAVAILABLE: "CAMPAIGN_API_UNAVAILABLE",
} as const;

export type AnalysisErrorCode = (typeof ANALYSIS_ERRORS)[keyof typeof ANALYSIS_ERRORS];
