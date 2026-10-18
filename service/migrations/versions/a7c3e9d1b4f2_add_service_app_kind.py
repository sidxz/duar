"""add service_apps.kind (MCP-server discriminator)

Additive column distinguishing an ordinary service app (``kind="service"``, the
default) from an MCP server (``kind="mcp_server"``) for the Duar MCP feature.
Existing rows backfill to ``service`` via the server_default; no existing auth
tier behavior changes.

Revision ID: a7c3e9d1b4f2
Revises: f2a9c4d7e1b5
Create Date: 2026-10-17 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a7c3e9d1b4f2"
down_revision: Union[str, None] = "f2a9c4d7e1b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "service_apps",
        sa.Column(
            "kind",
            sa.String(length=32),
            server_default="service",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("service_apps", "kind")
