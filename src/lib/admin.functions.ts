import { createServerFn } from "@tanstack/react-start";
import { z } from "zod";
import { toAuditPage, type AuditPage, type AuditPageRecord } from "./admin-audit";
import { isOwnerOf, ownedOrganizations, type AdminOrganization } from "./admin-access";
import { ADMIN_ERRORS, mapFastapiFailure, type AdminErrorCode } from "./admin-errors";
import {
  PERIOD_PATTERN,
  toUsageReport,
  type UsageReport,
  type UsageReportRecord,
} from "./admin-usage";
import type { MeResponse } from "./fastapi-client.server";

/**
 * Administrative server functions. Each one starts from the signed session cookie, checks that the user is an owner
 * of the selected organization, and only then asks FastAPI, which checks membership and role again.
 * The browser never sends a token, a user id or a role. These functions never query PostgreSQL or OpenRouter directly.
 */

export type { AdminOrganization };

const organizationIdSchema = z.string().uuid();

const usageInputSchema = z.object({
  organizationId: organizationIdSchema,
  period: z.string().regex(PERIOD_PATTERN),
});

const auditInputSchema = z.object({
  organizationId: organizationIdSchema,
  cursor: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER).optional(),
  limit: z.number().int().min(1).max(100).default(10),
});

/** Validation failures reach the browser as a stable code, never as a Zod message. */
function parseInput<T extends z.ZodTypeAny>(schema: T, input: unknown): z.infer<T> {
  const result = schema.safeParse(input);
  if (!result.success) throw new Error(ADMIN_ERRORS.INVALID_INPUT);
  return result.data;
}

/** Maps any failure to an administrative code. Upstream bodies and details never reach the browser. */
function adminErrorCode(error: unknown): AdminErrorCode {
  const known = Object.values(ADMIN_ERRORS) as string[];
  if (error instanceof Error && known.includes(error.message))
    return error.message as AdminErrorCode;
  if (error instanceof Error && error.name === "FastapiError") {
    const { status, code } = error as unknown as { status: number; code: string | null };
    return mapFastapiFailure(status, code);
  }
  return ADMIN_ERRORS.UNAVAILABLE;
}

async function sessionMemberships(token: string): Promise<MeResponse["organizations"]> {
  const { fastapiRequest } = await import("./fastapi-client.server");
  const me = await fastapiRequest<MeResponse>("/api/v1/me", { token, timeoutMs: 10_000 });
  return me.organizations;
}

/** Refuses before any call to the usage or audit endpoints when the organization is not one the user owns. */
async function requireOwner(token: string, organizationId: string): Promise<void> {
  if (!isOwnerOf(await sessionMemberships(token), organizationId)) {
    throw new Error(ADMIN_ERRORS.NOT_OWNER);
  }
}

/** The organizations this user owns. An empty list means the administrative screen shows no-access. */
export const getAdminOrganizations = createServerFn({ method: "GET" }).handler(
  async (): Promise<{ organizations: AdminOrganization[] }> => {
    const { currentAccessToken } = await import("./campaign-session.server");
    try {
      const token = await currentAccessToken();
      return { organizations: ownedOrganizations(await sessionMemberships(token)) };
    } catch (error) {
      console.error("Admin organizations unavailable");
      throw new Error(adminErrorCode(error));
    }
  },
);

/**
 * The monthly usage report. FastAPI audits this call (usage.report_accessed), so the screen calls it only when the owner
 * presses the button. Nothing here polls, prefetches or retries on its own.
 */
export const getUsageReport = createServerFn({ method: "GET" })
  .inputValidator((input: unknown) => parseInput(usageInputSchema, input))
  .handler(async ({ data: request }): Promise<UsageReport> => {
    const { currentAccessToken } = await import("./campaign-session.server");
    const { fastapiRequest } = await import("./fastapi-client.server");
    try {
      const token = await currentAccessToken();
      await requireOwner(token, request.organizationId);
      const record = await fastapiRequest<UsageReportRecord>(
        `/api/v1/organizations/${request.organizationId}/usage?period=${request.period}`,
        { token, timeoutMs: 15_000 },
      );
      return toUsageReport(record);
    } catch (error) {
      console.error("Usage report unavailable");
      throw new Error(adminErrorCode(error));
    }
  });

/** One page of the audit trail, newest first. Reading the trail is not itself audited. */
export const getAuditEvents = createServerFn({ method: "GET" })
  .inputValidator((input: unknown) => parseInput(auditInputSchema, input))
  .handler(async ({ data: request }): Promise<AuditPage> => {
    const { currentAccessToken } = await import("./campaign-session.server");
    const { fastapiRequest } = await import("./fastapi-client.server");
    const query = new URLSearchParams({ limit: String(request.limit) });
    if (request.cursor !== undefined) query.set("cursor", String(request.cursor));
    try {
      const token = await currentAccessToken();
      await requireOwner(token, request.organizationId);
      const record = await fastapiRequest<AuditPageRecord>(
        `/api/v1/organizations/${request.organizationId}/audit-events?${query.toString()}`,
        { token, timeoutMs: 10_000 },
      );
      return toAuditPage(record);
    } catch (error) {
      console.error("Audit events unavailable");
      throw new Error(adminErrorCode(error));
    }
  });
