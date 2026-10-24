"""Foundation schema: organizations, app users and organization memberships.

Revision ID: 0001_foundation
Revises:
Create Date: 2026-10-24

Decisions (documented in docs/PHASE2_FOUNDATION.md):
- UUID primary keys with a database default (gen_random_uuid, built in since PostgreSQL 13).
- Timestamps are timestamptz; the database sets them, so application time zones do not matter.
- organization_memberships enforces one row per (organization, user) and a closed set of roles.
- Foreign keys use ON DELETE RESTRICT: deleting an organization or user never cascades into membership history.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_organizations"),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
        sa.CheckConstraint("slug ~ '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$'", name="ck_organizations_slug_format"),
        sa.CheckConstraint("status IN ('active', 'suspended')", name="ck_organizations_status_valid"),
    )

    op.create_table(
        "app_users",
        sa.Column("id", sa.Uuid(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("external_subject", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_app_users"),
        sa.UniqueConstraint("external_subject", name="uq_app_users_external_subject"),
    )

    op.create_table(
        "organization_memberships",
        sa.Column("id", sa.Uuid(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_organization_memberships"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_organization_memberships_organization_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_users.id"],
            name="fk_organization_memberships_user_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_organization_memberships_org_user"),
        sa.CheckConstraint("role IN ('owner', 'analyst', 'client_viewer')", name="ck_organization_memberships_role_valid"),
    )
    op.create_index(
        "ix_organization_memberships_user_id", "organization_memberships", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_organization_memberships_user_id", table_name="organization_memberships")
    op.drop_table("organization_memberships")
    op.drop_table("app_users")
    op.drop_table("organizations")
