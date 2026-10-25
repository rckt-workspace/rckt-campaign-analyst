/**
 * Server-only helpers shared by the campaign server functions. Every call starts from the signed session cookie:
 * the browser never supplies a token, a user id or a role.
 */
import { ANALYSIS_ERRORS } from "./analysis-errors";
import { FastapiError, fastapiRequest, type MeResponse } from "./fastapi-client.server";
import { AuthNotConfiguredError, createSupabaseServerClient } from "./supabase/server";

/** Returns the access token of the current session, or throws SESSION_EXPIRED. */
export async function currentAccessToken(): Promise<string> {
  let supabase;
  try {
    supabase = createSupabaseServerClient();
  } catch (error) {
    if (error instanceof AuthNotConfiguredError) throw new Error(ANALYSIS_ERRORS.SESSION_EXPIRED);
    throw error;
  }

  const { data: claimsData, error: claimsError } = await supabase.auth.getClaims();
  if (claimsError || !claimsData) throw new Error(ANALYSIS_ERRORS.SESSION_EXPIRED);

  const { data: sessionData } = await supabase.auth.getSession();
  const token = sessionData.session?.access_token;
  if (!token) throw new Error(ANALYSIS_ERRORS.SESSION_EXPIRED);
  return token;
}

/**
 * The organization chosen in the UI is only a selection: FastAPI checks it against the user's memberships.
 * Without a selection, the first authorized organization is used.
 */
export async function resolveOrganizationId(token: string, requested?: string): Promise<string> {
  if (requested) return requested;
  const me = await fastapiRequest<MeResponse>("/api/v1/me", { token, timeoutMs: 10_000 });
  const first = me.organizations[0];
  if (!first) throw new Error(ANALYSIS_ERRORS.NO_ORGANIZATION);
  return first.id;
}

/** Maps a failed FastAPI call to a stable UI code. Upstream bodies and details never reach the browser. */
export function campaignErrorCode(error: unknown, fallback: string): string {
  if (
    error instanceof Error &&
    (Object.values(ANALYSIS_ERRORS) as string[]).includes(error.message)
  ) {
    return error.message;
  }
  if (error instanceof FastapiError) {
    if (error.status === 401) return ANALYSIS_ERRORS.SESSION_EXPIRED;
    if (error.status === 403) return ANALYSIS_ERRORS.FORBIDDEN;
    if (error.status === 404) return ANALYSIS_ERRORS.NO_ORGANIZATION;
    if (error.status === 422) return ANALYSIS_ERRORS.CAMPAIGN_INVALID;
  }
  return fallback;
}
