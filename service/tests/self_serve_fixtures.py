"""Shared helpers for the self-serve test files.

SQLite-backed engine over the subset of tables the onboarding flow touches
(same pattern as test_actions_insights.py — tables with Postgres ARRAY columns
are excluded), plus a signed-session-cookie builder so route tests can
authenticate to /onboard without driving an IdP.
"""

from __future__ import annotations

import json
from base64 import b64encode

from itsdangerous import TimestampSigner
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.ext.compiler import compiles

import src.models  # noqa: F401 — configure every mapper before create_all
from src.database import Base


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


# Gotcha for anyone hardcoding a UUID constant against this engine: SQLite gives
# a column declared `postgresql.UUID` (our models' type for every id/fk) NUMERIC
# affinity, since the literal type name "UUID" contains none of INT/CHAR/TEXT/
# BLOB/REAL. An all-digit hex UUID (e.g. "...0000-0000-000000000001") gets
# silently coerced to the Python int 1 on INSERT, then crashes on readback
# (`AttributeError: 'int' object has no attribute 'replace'` from uuid.UUID()).
# Real uuid4()-generated ids are safe (near-zero odds of being all-digit); a
# hand-picked test constant must include at least one hex letter (a-f).


TABLES = [
    "users",
    "social_accounts",
    "organizations",
    "organization_domains",
    "workspace_allowed_organizations",
    "workspaces",
    "workspace_memberships",
    "workspace_invitations",
    "activity_logs",
    "groups",
    "group_memberships",
    "roles",
    "user_roles",
    "resource_permissions",
    "resource_shares",
]


async def make_engine() -> AsyncEngine:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c, tables=[Base.metadata.tables[n] for n in TABLES]
            )
        )
    return engine


def session_cookie(data: dict, secret: str) -> str:
    """Encode ``data`` exactly as starlette.middleware.sessions does."""
    payload = b64encode(json.dumps(data).encode("utf-8"))
    return TimestampSigner(secret).sign(payload).decode("utf-8")
