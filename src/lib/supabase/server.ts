/**
 * Server-only Supabase client. It reads the session from HttpOnly cookies on each request, so tokens never reach
 * the browser. The browser has no Supabase client at all: every auth action is a server function.
 *
 * Values are runtime environment variables, not VITE_ build-time values, so the same image can be configured per
 * environment and no Supabase value is embedded in the client bundle.
 */
import { createServerClient } from "@supabase/ssr";
import { getCookies, setCookie } from "@tanstack/react-start/server";

export class AuthNotConfiguredError extends Error {
  constructor() {
    super("AUTH_NOT_CONFIGURED");
    this.name = "AuthNotConfiguredError";
  }
}

export function createSupabaseServerClient() {
  const url = process.env["SUPABASE_URL"]?.trim();
  const publishableKey = process.env["SUPABASE_PUBLISHABLE_KEY"]?.trim();
  if (!url || !publishableKey) {
    // Fail closed: without configuration no session can be established or trusted.
    console.error("Supabase server configuration is incomplete");
    throw new AuthNotConfiguredError();
  }

  return createServerClient(url, publishableKey, {
    cookies: {
      getAll() {
        return Object.entries(getCookies()).map(([name, value]) => ({ name, value }));
      },
      setAll(cookiesToSet) {
        for (const { name, value, options } of cookiesToSet) {
          // @supabase/ssr writes cookies readable by page scripts (it serves the browser client too).
          // This application has no browser client, so session cookies are forced to HttpOnly.
          setCookie(name, value, { ...options, httpOnly: true, sameSite: "lax", path: "/" });
        }
      },
    },
  });
}
