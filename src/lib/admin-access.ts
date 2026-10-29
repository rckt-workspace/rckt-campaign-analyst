/**
 * Who may open the administrative screen and which organizations it offers. Pure: the decision that the backend makes is
 * repeated here only to choose what to show. FastAPI still checks membership and role on every call.
 */

export type MembershipLike = { id: string; name: string; slug: string; role: string };

export type AdminOrganization = { id: string; name: string; slug: string };

/** Only organizations where the user is the owner. Analyst and client_viewer memberships are never offered. */
export function ownedOrganizations(memberships: readonly MembershipLike[]): AdminOrganization[] {
  return memberships
    .filter((membership) => membership.role === "owner")
    .map((membership) => ({ id: membership.id, name: membership.name, slug: membership.slug }));
}

/** True only when the organization id is one of the owned organizations. Nothing is ever chosen automatically otherwise. */
export function isOwnerOf(memberships: readonly MembershipLike[], organizationId: string): boolean {
  return memberships.some(
    (membership) => membership.role === "owner" && membership.id === organizationId,
  );
}
