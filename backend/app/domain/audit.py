"""Audit events: the allowed actions, their resource types, the metadata each action may carry, and validation.

Pure: no database. Every event is validated here before it reaches audit_events (migration 0008), so a field that is not
on the allow-list can never be stored. Metadata is built field by field in the code that records the event; arbitrary
request or provider objects are never copied into it.

Sources:
- user: an authenticated person; actor_user_id is set.
- internal: a process of the application (for example, finishing an analysis). No actor.
- operator_cli: an operator procedure run on the server. No actor: no verified person is behind it, and no actor is
  invented for it (docs/PHASE2_AUDIT.md).
"""

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.domain.analysis import SAFE_ERROR_CODES
from app.domain.quota import PLAN_VALUES
from app.domain.usage_report import PERIOD_PATTERN

AUDIT_SOURCES = ("user", "internal", "operator_cli")
AUDIT_ACTIONS = (
    "analysis.created",
    "analysis.succeeded",
    "analysis.failed",
    "quota.updated",
    "membership.granted",
    "usage.report_accessed",
)
AUDIT_RESOURCE_TYPES = ("analysis", "analysis_quota", "organization_membership", "usage_report")

ACTION_RESOURCE_TYPE = {
    "analysis.created": "analysis",
    "analysis.succeeded": "analysis",
    "analysis.failed": "analysis",
    "quota.updated": "analysis_quota",
    "membership.granted": "organization_membership",
    "usage.report_accessed": "usage_report",
}

MAX_RESOURCE_ID_LENGTH = 128
MAX_TEXT_LENGTH = 200
MAX_METADATA_BYTES = 2048
_MODEL_NAME = re.compile(r"^[A-Za-z0-9._/:-]{1,200}$")
_ROLE_VALUES = ("owner", "analyst", "client_viewer")

# The exact keys each action may carry, with their types. Nothing else is accepted.
# Value types: "str", "int", "bool", "model", "period", "plan", "role", "error_code". Nullable keys accept None.
ALLOWED_DETAILS: dict[str, dict[str, tuple[str, bool]]] = {
    "analysis.created": {"quota_period": ("period", True)},
    "analysis.succeeded": {
        "model_used": ("model", False),
        "used_fallback": ("bool", False),
        "format_valid": ("bool", False),
    },
    "analysis.failed": {
        "error_code": ("error_code", False),
        "used_fallback": ("bool", False),
    },
    "quota.updated": {
        "plan": ("plan", False),
        "monthly_analysis_limit": ("int", False),
        "previous_plan": ("plan", True),
        "previous_monthly_analysis_limit": ("int", True),
    },
    "membership.granted": {"role": ("role", False)},
    "usage.report_accessed": {"period": ("period", False)},
}


class AuditValidationError(ValueError):
    """An event does not follow the audit rules. Nothing is written when this is raised."""


def _check_value(action: str, key: str, kind: str, value: Any) -> None:
    if kind == "bool":
        if not isinstance(value, bool):
            raise AuditValidationError(f"{action}.{key} must be a boolean")
    elif kind == "int":
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2_147_483_647:
            raise AuditValidationError(f"{action}.{key} must be a non-negative integer")
    elif kind == "period":
        if not isinstance(value, str) or not re.match(PERIOD_PATTERN, value):
            raise AuditValidationError(f"{action}.{key} must be YYYY-MM")
    elif kind == "plan":
        if value not in PLAN_VALUES:
            raise AuditValidationError(f"{action}.{key} is not a known plan")
    elif kind == "role":
        if value not in _ROLE_VALUES:
            raise AuditValidationError(f"{action}.{key} is not a known role")
    elif kind == "error_code":
        if value not in SAFE_ERROR_CODES:
            raise AuditValidationError(f"{action}.{key} is not a safe error code")
    elif kind == "model":
        if not isinstance(value, str) or not _MODEL_NAME.match(value):
            raise AuditValidationError(f"{action}.{key} is not a model identifier")
    else:  # pragma: no cover - the table above is the only source of kinds
        raise AuditValidationError(f"unknown kind for {action}.{key}")


@dataclass(frozen=True)
class AuditDraft:
    """One event, validated on construction. The database receives only what passes here."""

    organization_id: uuid.UUID
    action: str
    resource_id: str
    source: str
    actor_user_id: uuid.UUID | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.action not in AUDIT_ACTIONS:
            raise AuditValidationError("unknown action")
        if self.source not in AUDIT_SOURCES:
            raise AuditValidationError("unknown source")
        if (self.source == "user") != (self.actor_user_id is not None):
            raise AuditValidationError("a user source needs an actor, and only a user source may have one")
        if not isinstance(self.resource_id, str) or not self.resource_id.strip():
            raise AuditValidationError("resource_id is required")
        if len(self.resource_id) > MAX_RESOURCE_ID_LENGTH:
            raise AuditValidationError("resource_id is too long")

        allowed = ALLOWED_DETAILS[self.action]
        unknown = set(self.details) - set(allowed)
        if unknown:
            raise AuditValidationError(f"{self.action} does not allow: {', '.join(sorted(unknown))}")
        missing = [key for key, (_, nullable) in allowed.items() if key not in self.details and not nullable]
        if missing:
            raise AuditValidationError(f"{self.action} requires: {', '.join(sorted(missing))}")
        for key, value in self.details.items():
            kind, nullable = allowed[key]
            if value is None:
                if not nullable:
                    raise AuditValidationError(f"{self.action}.{key} cannot be null")
                continue
            _check_value(self.action, key, kind, value)
        if len(json.dumps(self.details, separators=(",", ":"), ensure_ascii=False).encode("utf-8")) > MAX_METADATA_BYTES:
            raise AuditValidationError("metadata is too large")

    @property
    def resource_type(self) -> str:
        return ACTION_RESOURCE_TYPE[self.action]
