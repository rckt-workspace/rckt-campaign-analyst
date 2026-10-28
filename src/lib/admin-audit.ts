/**
 * Audit events as the administrative screen reads them. Pure: no React, no network.
 *
 * - metadata is dropped in the transformation. The screen never receives or renders arbitrary metadata.
 * - Order is the order FastAPI sends (id descending). The id is not presented as the commit order of transactions.
 * - Times are shown in UTC, and the zone is always written.
 */

export type AuditSource = "user" | "internal" | "operator_cli";

export type AuditEventRecord = {
  id: number;
  occurred_at: string;
  source: AuditSource;
  actor_user_id: string | null;
  action: string;
  resource_type: string;
  resource_id: string;
  metadata: unknown;
};

export type AuditPageRecord = { items: AuditEventRecord[]; next_cursor: number | null };

export type AuditEvent = {
  id: number;
  occurredAt: string;
  source: AuditSource;
  actorUserId: string | null;
  action: string;
  resourceType: string;
  resourceId: string;
};

export type AuditPage = { items: AuditEvent[]; nextCursor: number | null };

export function toAuditPage(record: AuditPageRecord): AuditPage {
  return {
    items: record.items.map((event) => ({
      id: event.id,
      occurredAt: event.occurred_at,
      source: event.source,
      actorUserId: event.actor_user_id,
      action: event.action,
      resourceType: event.resource_type,
      resourceId: event.resource_id,
    })),
    nextCursor: record.next_cursor,
  };
}

/** Spanish descriptions of the six audited actions. Unknown codes are shown as such, never as a guess. */
export const ACTION_LABELS: Record<string, string> = {
  "analysis.created": "Análisis solicitado",
  "analysis.succeeded": "Análisis completado",
  "analysis.failed": "Análisis sin resultado",
  "quota.updated": "Cuota modificada",
  "membership.granted": "Membresía concedida",
  "usage.report_accessed": "Informe de consumo consultado",
};

export const SOURCE_LABELS: Record<AuditSource, string> = {
  user: "Usuario",
  internal: "Proceso interno",
  operator_cli: "Operador (CLI del servidor)",
};

export const RESOURCE_LABELS: Record<string, string> = {
  analysis: "Análisis",
  analysis_quota: "Cuota de la organización",
  organization_membership: "Membresía",
  usage_report: "Informe de consumo",
};

export function describeAction(action: string): string {
  return ACTION_LABELS[action] ?? "Evento no reconocido";
}

export function describeResourceType(resourceType: string): string {
  return RESOURCE_LABELS[resourceType] ?? "Recurso";
}

/** A date and time in UTC, with the zone written out. Deterministic: it never depends on the viewer's zone. */
export function formatOccurredAt(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "Fecha no disponible";
  const formatted = new Intl.DateTimeFormat("es", {
    timeZone: "UTC",
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
  return `${formatted} UTC`;
}
