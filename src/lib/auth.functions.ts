import { createServerFn } from "@tanstack/react-start";
import { z } from "zod";

export type Account = {
  email: string | null;
  organizations: { id: string; slug: string; name: string; role: string }[];
};

const signInSchema = z.object({
  email: z.string().trim().email().max(320),
  password: z.string().min(1).max(200),
});

/**
 * Returns the signed-in account, or null when there is no valid session.
 * getClaims validates the token signature with the project's keys and refreshes it when needed.
 * FastAPI then confirms the identity and returns the organizations the user may access.
 */
export const getAccount = createServerFn({ method: "GET" }).handler(async (): Promise<Account | null> => {
  const { fastapiRequest, FastapiError } = await import("./fastapi-client.server");
  const { AuthNotConfiguredError, createSupabaseServerClient } = await import("./supabase/server");
  let supabase;
  try {
    supabase = createSupabaseServerClient();
  } catch (error) {
    // Not configured means no session can exist: the visitor sees the sign-in page, never the application.
    if (error instanceof AuthNotConfiguredError) return null;
    throw error;
  }

  const { data: claimsData, error: claimsError } = await supabase.auth.getClaims();
  if (claimsError || !claimsData) return null;

  const { data: sessionData } = await supabase.auth.getSession();
  const token = sessionData.session?.access_token;
  if (!token) return null;

  try {
    const me = await fastapiRequest<{ user: { email: string | null }; organizations: Account["organizations"] }>("/api/v1/me", { token, timeoutMs: 10_000 });
    return { email: me.user.email, organizations: me.organizations };
  } catch (error) {
    if (error instanceof FastapiError && error.status === 401) return null;
    throw new Error("ACCOUNT_UNAVAILABLE");
  }
});

export const signIn = createServerFn({ method: "POST" })
  .inputValidator((data: unknown) => signInSchema.parse(data))
  .handler(async ({ data }) => {
    const { AuthNotConfiguredError, createSupabaseServerClient } = await import("./supabase/server");
    let supabase;
    try {
      supabase = createSupabaseServerClient();
    } catch (error) {
      if (error instanceof AuthNotConfiguredError) throw new Error("AUTH_UNAVAILABLE");
      throw error;
    }

    const { error } = await supabase.auth.signInWithPassword({ email: data.email, password: data.password });
    if (error) {
      // 4xx: wrong credentials or unconfirmed account. Anything else is a service problem.
      if (error.status !== undefined && error.status >= 400 && error.status < 500) {
        throw new Error("INVALID_CREDENTIALS");
      }
      console.error("Supabase sign-in unavailable", error.status);
      throw new Error("AUTH_UNAVAILABLE");
    }
    return { ok: true as const };
  });

export const signOut = createServerFn({ method: "POST" }).handler(async () => {
  const { createSupabaseServerClient } = await import("./supabase/server");
  try {
    const supabase = createSupabaseServerClient();
    await supabase.auth.signOut({ scope: "local" });
  } catch (error) {
    // Signing out must always succeed locally: the cookies are cleared by the next response regardless.
    console.error("Sign-out reached the auth provider with an error", error instanceof Error ? error.name : "unknown");
  }
  return { ok: true as const };
});
