"""Organization roles and the rules tied to them. Pure functions, no database access."""

from enum import StrEnum


class Role(StrEnum):
    OWNER = "owner"
    ANALYST = "analyst"
    CLIENT_VIEWER = "client_viewer"


ROLE_VALUES = tuple(role.value for role in Role)

# Roles that may read organization data and campaign history.
VIEW_ROLES = frozenset(Role)
# Roles that may create campaigns and append metric entries. Entries are never edited or deleted.
RECORD_CAMPAIGN_ROLES = frozenset({Role.OWNER, Role.ANALYST})
# Roles that may edit or archive campaigns. Campaign lifecycle belongs to owners only.
CAMPAIGN_ADMIN_ROLES = frozenset({Role.OWNER})
# Roles that may start a request that consumes paid model tokens. client_viewer never can.
PAID_AI_ROLES = frozenset({Role.OWNER, Role.ANALYST})
# Roles that may manage members, invitations and usage reports.
ADMIN_ROLES = frozenset({Role.OWNER})


def can_run_paid_ai(role: Role) -> bool:
    return role in PAID_AI_ROLES
