"""add organizations and email-domain tenancy

Revision ID: d5e0f69a4c94
Revises: 21bddf454fbc
Create Date: 2026-06-09 15:43:41.691691

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d5e0f69a4c94"
down_revision: Union[str, None] = "21bddf454fbc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "is_public", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    # At most one public org.
    op.create_index(
        "uq_one_public_org",
        "organizations",
        ["is_public"],
        unique=True,
        postgresql_where=sa.text("is_public"),
    )
    op.create_table(
        "organization_domains",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column(
            "include_subdomains",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("domain", name="uq_org_domain"),
    )
    op.create_index(
        "ix_organization_domains_org_id",
        "organization_domains",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "workspace_allowed_organizations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        # RESTRICT: an org referenced by a workspace allow-list cannot be deleted
        # out from under it (a cascade could empty the list and flip the workspace
        # 'open to all'). delete_organization refuses it first; this is the backstop.
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "organization_id", name="uq_workspace_allowed_org"
        ),
    )
    op.create_index(
        "ix_workspace_allowed_orgs_workspace_id",
        "workspace_allowed_organizations",
        ["workspace_id"],
        unique=False,
    )
    op.add_column("users", sa.Column("organization_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_users_organization_id",
        "users",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_users_organization_id", "users", ["organization_id"], unique=False
    )

    # Seed the public org and backfill every existing user into it. Default
    # posture is public-ON, so deploying this change locks nobody out. The id is
    # a fixed sentinel UUID so it is stable/referenceable. PostgreSQL casts the
    # string literals to uuid/boolean in context.
    op.execute(
        "INSERT INTO organizations (id, slug, name, is_public, enabled) "
        "VALUES ('00000000-0000-0000-0000-000000000001', 'public', 'Public', "
        "true, true)"
    )
    op.execute(
        "UPDATE users SET organization_id = "
        "'00000000-0000-0000-0000-000000000001' WHERE organization_id IS NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_users_organization_id", table_name="users")
    op.drop_constraint("fk_users_organization_id", "users", type_="foreignkey")
    op.drop_column("users", "organization_id")
    op.drop_index(
        "ix_workspace_allowed_orgs_workspace_id",
        table_name="workspace_allowed_organizations",
    )
    op.drop_table("workspace_allowed_organizations")
    op.drop_index("ix_organization_domains_org_id", table_name="organization_domains")
    op.drop_table("organization_domains")
    op.drop_index("uq_one_public_org", table_name="organizations")
    op.drop_table("organizations")
