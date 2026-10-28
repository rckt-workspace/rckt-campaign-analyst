/**
 * Server-to-server calls from TanStack to FastAPI over the private network. Only server code imports this module.
 * The access token is forwarded from the HttpOnly session cookie; FastAPI verifies it and decides everything else.
 */

const DEFAULT_FASTAPI_URL = "http://127.0.0.1:8000";

export type MeResponse = {
  user: { id: string; email: string | null };
  organizations: { id: string; slug: string; name: string; role: string }[];
};

/**
 * Carries the HTTP status and, when FastAPI names it, a stable error code (for example AUDIT_UNAVAILABLE). The code
 * must match the safe shape below; the response body is never kept. Provider or service details never reach the browser.
 */
export class FastapiError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(status: number, code: string | null = null) {
    super(`FASTAPI_${status}`);
    this.name = "FastapiError";
    this.status = status;
    this.code = code;
  }
}

const SAFE_CODE = /^[A-Z][A-Z0-9_]{2,63}$/;

/** Reads only the `detail` of an error body, and only when it is a stable code. Anything else is discarded. */
async function safeErrorCode(response: Response): Promise<string | null> {
  try {
    const body = (await response.json()) as { detail?: unknown } | null;
    const detail = body?.detail;
    return typeof detail === "string" && SAFE_CODE.test(detail) ? detail : null;
  } catch {
    return null;
  }
}

export function fastapiBaseUrl(): string {
  return (process.env["FASTAPI_INTERNAL_URL"] ?? DEFAULT_FASTAPI_URL).replace(/\/+$/, "");
}

export async function fastapiRequest<T>(
  path: string,
  options: {
    token: string;
    method?: "GET" | "POST";
    body?: unknown;
    timeoutMs: number;
    /** Extra headers, such as Idempotency-Key. Never used for credentials: the token travels in Authorization. */
    headers?: Record<string, string>;
  },
): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeoutMs);
  const init: RequestInit = {
    method: options.method ?? "GET",
    headers: { ...options.headers, Authorization: `Bearer ${options.token}` },
    signal: controller.signal,
  };
  if (options.body !== undefined) {
    init.headers = { ...init.headers, "Content-Type": "application/json" };
    init.body = JSON.stringify(options.body);
  }
  try {
    const response = await fetch(`${fastapiBaseUrl()}${path}`, init);
    if (!response.ok) {
      const code = await safeErrorCode(response);
      console.error("FastAPI request failed", { path, status: response.status });
      throw new FastapiError(response.status, code);
    }
    return (await response.json()) as T;
  } finally {
    clearTimeout(timeout);
  }
}
