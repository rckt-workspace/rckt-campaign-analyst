import { test } from "node:test";
import assert from "node:assert/strict";
import {
  describeAction,
  formatOccurredAt,
  toAuditPage,
  type AuditEventRecord,
} from "./admin-audit";

function event(id: number, overrides: Partial<AuditEventRecord> = {}): AuditEventRecord {
  return {
    id,
    occurred_at: "2026-10-27T16:20:00Z",
    source: "user",
    actor_user_id: "00000000-0000-4000-8000-00000000a001",
    action: "analysis.created",
    resource_type: "analysis",
    resource_id: "00000000-0000-4000-8000-0000000000b1",
    metadata: { quota_period: "2026-10", prompt: "texto del usuario" },
    ...overrides,
  };
}

test("metadata never reaches the screen model, even when the backend sends it", () => {
  const page = toAuditPage({ items: [event(5)], next_cursor: null });
  assert.equal(Object.keys(page.items[0] ?? {}).includes("metadata"), false);
  assert.equal(JSON.stringify(page).includes("texto del usuario"), false);
});

test("the order and the cursor are kept exactly as the backend sends them", () => {
  const page = toAuditPage({ items: [event(9), event(7), event(3)], next_cursor: 3 });
  assert.deepEqual(
    page.items.map((item) => item.id),
    [9, 7, 3],
  );
  assert.equal(page.nextCursor, 3);
});

test("system events keep a null actor and are never attributed to a person", () => {
  const page = toAuditPage({
    items: [
      event(4, {
        source: "operator_cli",
        actor_user_id: null,
        action: "membership.granted",
        resource_type: "organization_membership",
      }),
    ],
    next_cursor: null,
  });
  assert.equal(page.items[0]?.actorUserId, null);
  assert.equal(page.items[0]?.source, "operator_cli");
});

test("every audited action has a Spanish description and an unknown one is never guessed", () => {
  assert.equal(describeAction("analysis.created"), "Análisis solicitado");
  assert.equal(describeAction("usage.report_accessed"), "Informe de consumo consultado");
  assert.equal(describeAction("platform.something_new"), "Evento no reconocido");
});

test("times are always written in UTC, whatever the viewer's zone", () => {
  assert.match(formatOccurredAt("2026-10-27T16:20:00Z"), /UTC$/);
  assert.equal(formatOccurredAt("not-a-date"), "Fecha no disponible");
});
