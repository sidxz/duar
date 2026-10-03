"""add action_usage (daily rollup of allowed RBAC action checks)

One upserted counter per (day, workspace, user, service, action) — the
usage side of role mining / dormant-grant detection. Denied checks land in
activity_logs as action_denied events, not here.

Revision ID: e4b7a2c9d1f3
Revises: c8e1a4f7d3b9
Create Date: 2026-07-25 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "e4b7a2c9d1f3"
down_revision: Union[str, None] = "c8e1a4f7d3b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "action_usage",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("service_name", sa.Text(), primary_key=True),
        sa.Column("action", sa.Text(), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
    )
    op.create_index("ix_action_usage_workspace_id", "action_usage", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_action_usage_workspace_id", table_name="action_usage")
    op.drop_table("action_usage")
