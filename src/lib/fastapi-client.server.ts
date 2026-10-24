/**
 * Server-to-server calls from TanStack to FastAPI over the private network. Only server code imports this module.
 * The access token is forwarded from the HttpOnly session cookie; FastAPI verifies it and decides everything else.
 */

const DEFAULT_FASTAPI_URL = "http://127.0.0.1:8000";

export type MeResponse = {
  user: { id: string; email: string | null };
  organizations: { id: string; slug: string; name: string; role: string }[];
};

/** Carries only the HTTP status. Provider or service details never reach the browser. */
export class FastapiError extends Error {
  readonly status: number;

  constructor(status: number) {
    super(`FASTAPI_${status}`);
    this.name = "FastapiError";
    this.status = status;
  }
}

export function fastapiBaseUrl(): string {
  return (process.env["FASTAPI_INTERNAL_URL"] ?? DEFAULT_FASTAPI_URL).replace(/\/+$/, "");
}

export async function fastapiRequest<T>(
  path: string,
  options: { token: string; method?: "GET" | "POST"; body?: unknown; timeoutMs: number },
): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeoutMs);
  const init: RequestInit = {
    method: options.method ?? "GET",
    headers: { Authorization: `Bearer ${options.token}` },
    signal: controller.signal,
  };
  if (options.body !== undefined) {
    init.headers = { ...init.headers, "Content-Type": "application/json" };
    init.body = JSON.stringify(options.body);
  }
  try {
    const response = await fetch(`${fastapiBaseUrl()}${path}`, init);
    if (!response.ok) {
      console.error("FastAPI request failed", { path, status: response.status });
      throw new FastapiError(response.status);
    }
    return (await response.json()) as T;
  } finally {
    clearTimeout(timeout);
  }
}
