"""add service_apps.allowed_idp_audiences (per-app IdP audience binding)

Binds an IdP token to the app it was issued for at /authz/resolve. When set, the
token's ``aud`` must be one of the app's registered OIDC client_id(s); a token
minted for one app can no longer mint via another app's service key. Empty
(default) preserves prior behavior (fall back to the deployment-wide audience).

Revision ID: f1a9c0b3d2e4
Revises: d5e0f69a4c94
Create Date: 2026-06-17 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "f1a9c0b3d2e4"
down_revision: Union[str, None] = "d5e0f69a4c94"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "service_apps",
        sa.Column(
            "allowed_idp_audiences",
            postgresql.ARRAY(sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("service_apps", "allowed_idp_audiences")
