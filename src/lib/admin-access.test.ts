import { test } from "node:test";
import assert from "node:assert/strict";
import { isOwnerOf, ownedOrganizations, type MembershipLike } from "./admin-access";
import { ADMIN_ERRORS, adminMessage, mapFastapiFailure } from "./admin-errors";

const memberships: MembershipLike[] = [
  {
    id: "00000000-0000-4000-8000-0000000000a1",
    name: "Cliente A",
    slug: "cliente-a",
    role: "owner",
  },
  {
    id: "00000000-0000-4000-8000-0000000000b1",
    name: "Cliente B",
    slug: "cliente-b",
    role: "analyst",
  },
  {
    id: "00000000-0000-4000-8000-0000000000c1",
    name: "Cliente C",
    slug: "cliente-c",
    role: "client_viewer",
  },
  {
    id: "00000000-0000-4000-8000-0000000000d1",
    name: "Cliente D",
    slug: "cliente-d",
    role: "owner",
  },
];

test("only owner memberships are offered, in the order the session gave them", () => {
  assert.deepEqual(
    ownedOrganizations(memberships).map((organization) => organization.slug),
    ["cliente-a", "cliente-d"],
  );
});

test("a user without any owner membership is offered no organization at all", () => {
  const viewerOnly = memberships.filter((membership) => membership.role !== "owner");
  assert.deepEqual(ownedOrganizations(viewerOnly), []);
});

test("an analyst or viewer organization is never accepted as an administrative context", () => {
  assert.equal(isOwnerOf(memberships, "00000000-0000-4000-8000-0000000000b1"), false);
  assert.equal(isOwnerOf(memberships, "00000000-0000-4000-8000-0000000000c1"), false);
  assert.equal(isOwnerOf(memberships, "00000000-0000-4000-8000-0000000000a1"), true);
});

test("an organization the user does not belong to is refused", () => {
  assert.equal(isOwnerOf(memberships, "00000000-0000-4000-8000-0000000000ff"), false);
});

test("FastAPI statuses map to stable codes, and only a named audit refusal becomes the audit code", () => {
  assert.equal(mapFastapiFailure(401, null), ADMIN_ERRORS.SESSION_EXPIRED);
  assert.equal(mapFastapiFailure(403, null), ADMIN_ERRORS.FORBIDDEN);
  assert.equal(mapFastapiFailure(404, null), ADMIN_ERRORS.NOT_FOUND);
  assert.equal(mapFastapiFailure(422, null), ADMIN_ERRORS.INVALID_INPUT);
  assert.equal(mapFastapiFailure(503, "AUDIT_UNAVAILABLE"), ADMIN_ERRORS.AUDIT_UNAVAILABLE);
  // A generic 503 is not presented as an audit problem.
  assert.equal(mapFastapiFailure(503, "QUOTA_CONFIGURATION_INVALID"), ADMIN_ERRORS.UNAVAILABLE);
  assert.equal(mapFastapiFailure(500, null), ADMIN_ERRORS.UNAVAILABLE);
});

test("every code has a safe message, and an unknown code falls back to the generic one", () => {
  for (const code of Object.values(ADMIN_ERRORS)) {
    const message = adminMessage(code);
    assert.ok(message.length > 0);
    assert.doesNotMatch(message, /FASTAPI|status|\b5\d\d\b|stack|token|secret/i);
  }
  assert.equal(adminMessage("SOMETHING_ELSE"), adminMessage(ADMIN_ERRORS.UNAVAILABLE));
});
