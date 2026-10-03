"""add realms table + service_apps.realm_id (trusted app groups / shared scope)

Realms group service apps into one shared permission scope + token audience. A
member's ``realm_id`` points at its realm; effective scope becomes the realm slug.
Nullable column => non-breaking (standalone apps keep their own service_name scope).

Revision ID: b2c4d6e8f0a1
Revises: f1a9c0b3d2e4
Create Date: 2026-06-25 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2c4d6e8f0a1"
down_revision: Union[str, None] = "f1a9c0b3d2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "realms",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(255), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("m2m_ttl_s", sa.Integer(), server_default="300", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.add_column(
        "service_apps",
        sa.Column("realm_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_service_apps_realm_id",
        "service_apps",
        "realms",
        ["realm_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_service_apps_realm_id", "service_apps", type_="foreignkey")
    op.drop_column("service_apps", "realm_id")
    op.drop_table("realms")
