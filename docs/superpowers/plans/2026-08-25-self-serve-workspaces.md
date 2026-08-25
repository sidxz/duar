# Self-serve workspaces (hosted onboarding + invitations) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Flag-gated, Duar-hosted `/onboard` pages where a public-instance user joins a workspace through a one-time invitation link or creates one, plus the invitation model and the legacy-route gates — with the consortium instance byte-identical while the flag is off.

**Architecture:** Server-rendered Jinja2 pages (no JavaScript) under `service/src/api/onboard_routes.py`, authenticated by Duar's existing authlib OAuth client flow and the existing signed session cookie. Business logic in `invitation_service.py` (create / list / revoke / peek / redeem with an atomic single-use claim) and `workspace_service.create_self_serve` (cap + breaker + generated slug). One new table, `workspace_invitations`. The IdP profile extraction shared by the proxy and admin callbacks is factored into `_idp_profile` so the onboarding callback is a third caller, not a third copy.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Alembic, authlib (Starlette client), Starlette `SessionMiddleware`, Jinja2 (new explicit dependency), slowapi, pytest + pytest-asyncio + aiosqlite (SQLite-backed service tests).

**Spec:** `docs/superpowers/specs/2026-08-25-self-serve-workspaces-design.md` — read it first; every task below cites the section it implements.

## Global Constraints

- Flag `SELF_SERVE_ENABLED` defaults to `false`; with it unset the **only** observable change is `POST /workspaces` → 403 (spec §Decisions, §6.9).
- Settings (spec §1): `SELF_SERVE_ENABLED=false`, `SELF_SERVE_MAX_WORKSPACES_PER_USER=1` (`0` = join-only), `SELF_SERVE_MAX_CREATES_PER_HOUR=30`. No new rate-limit tier.
- Invitation constants: TTL 7 days, ≤ 50 pending per workspace, role ∈ {admin, editor, viewer}, code = `secrets.token_urlsafe(32)` stored as SHA-256 hex (spec §2–3).
- Hosted pages: `X-CSP-Override: html-page`, zero JavaScript, every mutation is a POST with `csrf`, session checked before CSRF (no session ⇒ 302, never 403), all hrefs / form actions / `Location`s absolute from `settings.base_url` (spec §4).
- Session rules: never `request.session.clear()` in onboarding code; only `onboard_*` keys are touched; `GET /onboard` never deletes `onboard_user_id` / `onboard_csrf` (spec §4).
- Naming: activity actions snake_case (`workspace_created`, `self_serve_denied`, `invitation_created|accepted|revoked|rejected`); `log_security` events dotted (`workspace.self_serve.created|denied`, `invitation.created|accepted|revoked|rejected`); plaintext code and hash appear in neither (spec §3).
- Python 3.12, `ruff` clean (`make lint`), commit after every task. Tests: `cd service && uv run pytest tests/<file> -v`.
- One whole-branch review at the end, not per task (project rule).

---

## File structure

| File | Responsibility |
|---|---|
| `service/src/config.py` | three `self_serve_*` settings |
| `service/src/api/workspace_routes.py` | flag gates on `POST /workspaces` (→ `create_self_serve`) and direct-add |
| `service/src/api/admin_routes.py`, `service/src/schemas/admin.py`, `admin/src/types/api.ts` | `self_serve` block in `/admin/system/settings` |
| `service/src/middleware/security_headers.py` | `form-action 'self'` in `_HTML_CSP`; `/onboard` no-store |
| `service/src/models/invitation.py` (+ `models/__init__.py`) | `WorkspaceInvitation` |
| `service/migrations/versions/f2a9c4d7e1b5_add_workspace_invitations.py` | table + index |
| `service/src/services/workspace_service.py` | `slugify`, `count_created_by`, `get_member_role`, `list_admin_workspaces`, `create_self_serve`, self-serve exceptions |
| `service/src/services/invitation_service.py` | `create`, `list_for_workspace`, `revoke`, `peek`, `redeem`, `InvitationInvalid` |
| `service/src/api/auth_routes.py` | `IdpProfile`, `IdpProfileError`, `_idp_profile`, `_profile_error_page`; `_error_page(back_href=)` |
| `service/src/api/onboard_routes.py` | all `/onboard` routes |
| `service/src/templates/onboard/{base,login,home,invites,done}.html` | pages |
| `service/src/main.py` | router registration, startup warning |
| `admin/src/pages/Activity.tsx`, `admin/src/components/charts.tsx` | action allowlists |
| `service/tests/self_serve_fixtures.py` | SQLite engine + session-cookie helpers shared by the new tests |
| `docs/guide/self-serve.md`, `mkdocs.yml`, `docs/deployment/environment.md`, `docs/getting-started/configuration.md`, `docs/security.md`, `docs/api/resources.md`, `docs/guide/workspaces.md`, `CHANGELOG.md` | docs |

---

### Task 1: Settings + legacy route gates + admin settings mirror

Implements spec §1 (settings, JSON mirror) and §4 "Legacy route changes" (the 403s; the `create_self_serve` rewiring lands in Task 4).

**Files:**
- Modify: `service/src/config.py` (after the `signal_*` block, ~line 116)
- Modify: `service/src/api/workspace_routes.py:41-67` and `:160-192`
- Modify: `service/src/schemas/admin.py:244-250`, `service/src/api/admin_routes.py:273-297`
- Modify: `admin/src/types/api.ts:407-414`
- Modify: `service/tests/test_workspace_audit_events.py` (the create test, ~line 85)
- Create: `service/tests/test_self_serve_gates.py`

**Interfaces:**
- Produces: `settings.self_serve_enabled: bool`, `settings.self_serve_max_workspaces_per_user: int`, `settings.self_serve_max_creates_per_hour: int`.

- [ ] **Step 1: Write the failing tests**

`service/tests/test_self_serve_gates.py`:

```python
"""SELF_SERVE_ENABLED gates on the legacy proxy-mode workspace routes.

Off (default): POST /workspaces is 403 — it was open to any user already holding a
workspace-scoped access token. On: direct member add is 403 (consent rule; use
invitations). Same fake-dep style as test_workspace_audit_events.py.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies import CurrentUser, get_current_user
from src.api.workspace_routes import router as workspace_router
from src.config import settings
from src.database import get_db
from src.middleware.rate_limit import limiter

WS_ID = uuid.uuid4()
ACTOR_ID = uuid.uuid4()


@pytest.fixture(autouse=True)
def _disable_limiter():
    original = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = original


class _FakeDB:
    async def commit(self):
        pass


def _client(role="owner") -> TestClient:
    app = FastAPI()
    app.include_router(workspace_router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=ACTOR_ID, workspace_id=WS_ID, workspace_role=role, groups=[]
    )

    async def _db():
        yield _FakeDB()

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def test_settings_defaults():
    assert settings.self_serve_enabled is False
    assert settings.self_serve_max_workspaces_per_user == 1
    assert settings.self_serve_max_creates_per_hour == 30


def test_create_workspace_403_when_flag_off(monkeypatch):
    monkeypatch.setattr(settings, "self_serve_enabled", False)
    resp = _client().post("/workspaces", json={"name": "Acme", "slug": "acme"})
    assert resp.status_code == 403
    assert "disabled" in resp.json()["detail"]


def test_direct_add_403_when_flag_on(monkeypatch):
    monkeypatch.setattr(settings, "self_serve_enabled", True)
    resp = _client().post(
        f"/workspaces/{WS_ID}/members/invite",
        json={"email": "a@example.com", "role": "viewer"},
    )
    assert resp.status_code == 403
    assert "invitations" in resp.json()["detail"]


def test_direct_add_unchanged_when_flag_off(monkeypatch):
    """Flag off must not touch the legacy path: it reaches the service layer
    (which here raises 'User not found' through the fake) — i.e. NOT a 403."""
    monkeypatch.setattr(settings, "self_serve_enabled", False)
    from src.api import workspace_routes

    async def _invite(*a, **kw):
        raise ValueError("User not found")

    monkeypatch.setattr(workspace_routes.workspace_service, "invite_member", _invite)
    resp = _client().post(
        f"/workspaces/{WS_ID}/members/invite",
        json={"email": "a@example.com", "role": "viewer"},
    )
    assert resp.status_code == 400
```

In `service/tests/test_workspace_audit_events.py`, find the test that POSTs `/workspaces` (around line 85) and add the flag at its top:

```python
    monkeypatch.setattr(settings, "self_serve_enabled", True)
```

with `from src.config import settings` added to that file's imports.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd service && uv run pytest tests/test_self_serve_gates.py -v`
Expected: `test_settings_defaults` FAILS with `AttributeError: 'Settings' object has no attribute 'self_serve_enabled'`; the 403 tests fail with 201/400.

- [ ] **Step 3: Add the settings**

In `service/src/config.py`, after the `signal_stuffing_distinct_emails` line:

```python
    # Self-serve workspaces — public instances only (docs/guide/self-serve.md).
    # Off: /onboard* is 404 and POST /workspaces is 403. Consortium deployments
    # leave this unset.
    self_serve_enabled: bool = False
    self_serve_max_workspaces_per_user: int = 1  # created_by count; 0 = join-only
    self_serve_max_creates_per_hour: int = 30  # instance-wide, successful creates
```

- [ ] **Step 4: Gate the legacy routes**

In `service/src/api/workspace_routes.py`, `create_workspace` — insert as the first statement of the function body:

```python
    if not settings.self_serve_enabled:
        raise HTTPException(
            status_code=403, detail="Workspace creation is disabled on this server"
        )
```

In `invite_member` (the `/{workspace_id}/members/invite` route), insert as the first statement:

```python
    if settings.self_serve_enabled:
        # Consent rule (spec): in self-serve mode nobody is added to a workspace
        # without their own action, and this endpoint is an email-existence oracle.
        raise HTTPException(
            status_code=403,
            detail="Direct member add is disabled in self-serve mode; use invitations",
        )
```

- [ ] **Step 5: Mirror the settings in `/admin/system/settings`**

`service/src/schemas/admin.py` — before `class SystemSettingsResponse`:

```python
class SelfServeInfo(BaseModel):
    enabled: bool
    max_workspaces_per_user: int
    max_creates_per_hour: int
```

and add the field `self_serve: SelfServeInfo` to `SystemSettingsResponse`.

`service/src/api/admin_routes.py`, in `system_settings`, after `service_info = {...}`:

```python
    self_serve = {
        "enabled": settings.self_serve_enabled,
        "max_workspaces_per_user": settings.self_serve_max_workspaces_per_user,
        "max_creates_per_hour": settings.self_serve_max_creates_per_hour,
    }
```

and pass `self_serve=self_serve,` to the `SystemSettingsResponse(...)` constructor.

`admin/src/types/api.ts` — before `export interface SystemSettings`:

```ts
export interface SelfServeInfo {
  enabled: boolean;
  max_workspaces_per_user: number;
  max_creates_per_hour: number;
}
```

and add `self_serve: SelfServeInfo;` to `SystemSettings`.

- [ ] **Step 6: Run the tests**

Run: `cd service && uv run pytest tests/test_self_serve_gates.py tests/test_workspace_audit_events.py -v`
Expected: all PASS.

Run: `cd admin && npx tsc -b --noEmit` (or `npm run build`) — Expected: clean.

- [ ] **Step 7: Lint and commit**

```bash
make lint
git add service/src/config.py service/src/api/workspace_routes.py service/src/schemas/admin.py service/src/api/admin_routes.py admin/src/types/api.ts service/tests/test_self_serve_gates.py service/tests/test_workspace_audit_events.py
git commit -m "feat(self-serve): SELF_SERVE_ENABLED settings + legacy workspace route gates"
```

---

### Task 2: Security headers — `form-action 'self'` and `/onboard` no-store

Implements spec §4 "Middleware changes".

**Files:**
- Modify: `service/src/middleware/security_headers.py:110-131`
- Modify: `service/tests/test_security_headers_csp.py`

- [ ] **Step 1: Update / add the tests**

In `service/tests/test_security_headers_csp.py`, change `test_html_override_csp_defines_form_action_and_base_uri` to:

```python
def test_html_override_csp_allows_same_origin_forms_only():
    # Hosted onboarding pages POST plain forms to Duar itself; nowhere else.
    csp = _csp("/html")
    assert "form-action 'self'" in csp
    assert "form-action 'none'" not in csp
    assert "base-uri 'none'" in csp
```

Add to `_app()` a third route and two tests:

```python
    @app.get("/onboard/home")
    def onboard():
        resp = PlainTextResponse("<html></html>")
        resp.headers["X-CSP-Override"] = "html-page"
        return resp
```

```python
def test_onboard_pages_are_no_store():
    resp = TestClient(_app()).get("/onboard/home")
    assert resp.headers["Cache-Control"] == "no-store"


def test_plain_api_route_is_not_no_store():
    resp = TestClient(_app()).get("/plain")
    assert resp.headers.get("Cache-Control") != "no-store"
```

Also update the module docstring's sentence "must pin `form-action` and `base-uri` to `'none'`" to "the API CSP pins both to `'none'`; the HTML override pins `base-uri 'none'` and `form-action 'self'` (hosted forms)".

- [ ] **Step 2: Run to verify failure**

Run: `cd service && uv run pytest tests/test_security_headers_csp.py -v`
Expected: the two new tests + the renamed one FAIL.

- [ ] **Step 3: Implement**

In `service/src/middleware/security_headers.py`:

```python
        if (
            path.startswith("/auth")
            or path.startswith("/admin")
            or path.startswith("/users")
            or path.startswith("/onboard")  # one-time invite links, personal data
        ):
```

and in `_HTML_CSP` replace `form-action 'none'` with `form-action 'self'`, updating the comment above it to:

```python
        # `form-action`/`base-uri` are no-fallback directives: `default-src 'none'` does NOT
        # cover them, so they must be pinned explicitly. HTML pages may submit plain forms
        # to Duar itself ('self') — the hosted /onboard pages need it; error pages have no
        # forms so it is harmless there. (`style-src 'unsafe-inline'` is a deliberate
        # trade-off so the rendered HTML can use inline styles.)
```

- [ ] **Step 4: Run tests**

Run: `cd service && uv run pytest tests/test_security_headers_csp.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add service/src/middleware/security_headers.py service/tests/test_security_headers_csp.py
git commit -m "fix(security-headers): form-action 'self' on HTML pages; /onboard is no-store"
```

---

### Task 3: `WorkspaceInvitation` model + migration + shared SQLite fixtures

Implements spec §2.

**Files:**
- Create: `service/src/models/invitation.py`
- Modify: `service/src/models/__init__.py`
- Create: `service/migrations/versions/f2a9c4d7e1b5_add_workspace_invitations.py`
- Create: `service/tests/self_serve_fixtures.py`
- Create: `service/tests/test_invitation_model.py`

**Interfaces:**
- Produces: `src.models.invitation.WorkspaceInvitation` with columns `id, workspace_id, code_hash, email, role, created_by, created_at, expires_at, accepted_by, accepted_at, revoked_at`.
- Produces (tests): `self_serve_fixtures.make_engine()`, `self_serve_fixtures.TABLES`, `self_serve_fixtures.session_cookie(data, secret)`.

- [ ] **Step 1: Write the shared test fixtures module**

`service/tests/self_serve_fixtures.py` (not `test_`-prefixed, so pytest does not collect it):

```python
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
```

- [ ] **Step 2: Write the failing model test**

`service/tests/test_invitation_model.py`:

```python
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.invitation import WorkspaceInvitation
from src.models.user import User
from src.models.workspace import Workspace
from tests.self_serve_fixtures import make_engine


@pytest_asyncio.fixture
async def db():
    engine = await make_engine()
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


async def _ws(db) -> Workspace:
    user = User(email="o@example.com", name="Owner")
    db.add(user)
    await db.flush()
    ws = Workspace(name="Acme", slug="acme", created_by=user.id)
    db.add(ws)
    await db.flush()
    return ws


@pytest.mark.asyncio
async def test_role_check_constraint(db):
    ws = await _ws(db)
    db.add(
        WorkspaceInvitation(
            workspace_id=ws.id,
            code_hash="h1",
            role="owner",
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()


@pytest.mark.asyncio
async def test_code_hash_unique(db):
    ws = await _ws(db)
    exp = datetime.now(UTC) + timedelta(days=7)
    db.add(WorkspaceInvitation(workspace_id=ws.id, code_hash="h", role="viewer", expires_at=exp))
    await db.flush()
    db.add(WorkspaceInvitation(workspace_id=ws.id, code_hash="h", role="viewer", expires_at=exp))
    with pytest.raises(IntegrityError):
        await db.flush()
```

Run: `cd service && uv run pytest tests/test_invitation_model.py -v` — Expected: FAIL with `ModuleNotFoundError: src.models.invitation`.

- [ ] **Step 3: Write the model**

`service/src/models/invitation.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.database import Base


class WorkspaceInvitation(Base):
    """A one-time join link for a workspace (self-serve mode).

    ``code_hash`` is the SHA-256 of the bearer code; the plaintext is shown to
    the inviter once and never stored. ``email`` is an optional lock: when set,
    only a user signed in with that (IdP-verified) address can redeem.
    Pending == accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now.
    """

    __tablename__ = "workspace_invitations"
    __table_args__ = (
        CheckConstraint(
            "role IN ('admin', 'editor', 'viewer')", name="ck_invitation_role"
        ),
        Index("ix_invitations_workspace", "workspace_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    code_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

In `service/src/models/__init__.py` add `from src.models.invitation import WorkspaceInvitation` and `"WorkspaceInvitation",` to `__all__`.

- [ ] **Step 4: Write the migration**

`service/migrations/versions/f2a9c4d7e1b5_add_workspace_invitations.py`:

```python
"""add workspace_invitations (self-serve one-time join links)

Revision ID: f2a9c4d7e1b5
Revises: e4b7a2c9d1f3
Create Date: 2026-08-25 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "f2a9c4d7e1b5"
down_revision: Union[str, None] = "e4b7a2c9d1f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workspace_invitations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "accepted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "role IN ('admin', 'editor', 'viewer')", name="ck_invitation_role"
        ),
    )
    op.create_index(
        "ix_invitations_workspace", "workspace_invitations", ["workspace_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_invitations_workspace", table_name="workspace_invitations")
    op.drop_table("workspace_invitations")
```

- [ ] **Step 5: Run the model test and the migration**

Run: `cd service && uv run pytest tests/test_invitation_model.py -v` — Expected: PASS.

Run (dev containers up, `make start` not required): `cd service && uv run alembic upgrade head && uv run alembic heads` — Expected: `f2a9c4d7e1b5 (head)`. Then `uv run alembic downgrade -1 && uv run alembic upgrade head` — Expected: both succeed.

- [ ] **Step 6: Commit**

```bash
git add service/src/models/invitation.py service/src/models/__init__.py service/migrations/versions/f2a9c4d7e1b5_add_workspace_invitations.py service/tests/self_serve_fixtures.py service/tests/test_invitation_model.py
git commit -m "feat(self-serve): workspace_invitations model + migration"
```

---

### Task 4: `workspace_service` — self-serve create, slug, helpers, and the API rewiring

Implements spec §3 (`create_self_serve`) and §4 legacy `POST /workspaces` behavior when the flag is on.

**Files:**
- Modify: `service/src/services/workspace_service.py`
- Modify: `service/src/api/workspace_routes.py:41-67`
- Create: `service/tests/test_self_serve_workspace.py`
- Modify: `service/tests/test_self_serve_gates.py`

**Interfaces:**
- Produces:
  - `class SelfServeDisabled(Exception)`, `class SelfServeCapReached(Exception)`, `class SelfServeThrottled(Exception)`
  - `slugify(name: str) -> str`
  - `async count_created_by(db, user_id: uuid.UUID) -> int`
  - `async get_member_role(db, workspace_id: uuid.UUID, user_id: uuid.UUID) -> str | None`
  - `async list_admin_workspaces(db, user_id: uuid.UUID) -> list[tuple[Workspace, str]]`
  - `async create_self_serve(db, user: User, name: str, *, slug: str | None = None, now: datetime | None = None) -> Workspace`

- [ ] **Step 1: Write the failing tests**

`service/tests/test_self_serve_workspace.py`:

```python
"""create_self_serve: flag, per-user cap, hourly breaker, generated slug."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models.user import User
from src.models.workspace import Workspace, WorkspaceMembership
from src.services import workspace_service
from src.services.workspace_service import (
    SelfServeCapReached,
    SelfServeDisabled,
    SelfServeThrottled,
    create_self_serve,
    slugify,
)
from tests.self_serve_fixtures import make_engine

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def db():
    engine = await make_engine()
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    monkeypatch.setattr(settings, "self_serve_enabled", True)
    monkeypatch.setattr(settings, "self_serve_max_workspaces_per_user", 1)
    monkeypatch.setattr(settings, "self_serve_max_creates_per_hour", 30)


async def _user(db, email="u@example.com") -> User:
    user = User(email=email, name="U")
    db.add(user)
    await db.flush()
    return user


def test_slugify():
    assert slugify("Acme Corp!") == "acme-corp"
    assert slugify("  --Hello__World-- ") == "hello-world"
    assert slugify("!!!") == "ws"
    assert len(slugify("x" * 100)) == 40


@pytest.mark.asyncio
async def test_creates_workspace_with_generated_slug_and_owner(db):
    user = await _user(db)
    ws = await create_self_serve(db, user, "Acme Corp", now=NOW)
    assert ws.slug.startswith("acme-corp-") and len(ws.slug) == len("acme-corp-") + 4
    assert ws.created_by == user.id
    role = await workspace_service.get_member_role(db, ws.id, user.id)
    assert role == "owner"


@pytest.mark.asyncio
async def test_flag_off_raises(db, monkeypatch):
    monkeypatch.setattr(settings, "self_serve_enabled", False)
    user = await _user(db)
    with pytest.raises(SelfServeDisabled):
        await create_self_serve(db, user, "Acme", now=NOW)


@pytest.mark.asyncio
async def test_cap_counts_created_by(db):
    user = await _user(db)
    await create_self_serve(db, user, "One", now=NOW)
    with pytest.raises(SelfServeCapReached):
        await create_self_serve(db, user, "Two", now=NOW)
    assert await workspace_service.count_created_by(db, user.id) == 1


@pytest.mark.asyncio
async def test_cap_zero_is_join_only(db, monkeypatch):
    monkeypatch.setattr(settings, "self_serve_max_workspaces_per_user", 0)
    user = await _user(db)
    with pytest.raises(SelfServeCapReached):
        await create_self_serve(db, user, "One", now=NOW)


@pytest.mark.asyncio
async def test_breaker_counts_recent_workspaces_instance_wide(db, monkeypatch):
    monkeypatch.setattr(settings, "self_serve_max_creates_per_hour", 2)
    other = await _user(db, "o@example.com")
    for i in range(2):
        db.add(
            Workspace(
                name=f"w{i}", slug=f"w{i}", created_by=other.id,
                created_at=NOW - timedelta(minutes=10),
            )
        )
    await db.flush()
    user = await _user(db)
    with pytest.raises(SelfServeThrottled):
        await create_self_serve(db, user, "Mine", now=NOW)


@pytest.mark.asyncio
async def test_breaker_ignores_old_workspaces(db, monkeypatch):
    monkeypatch.setattr(settings, "self_serve_max_creates_per_hour", 1)
    other = await _user(db, "o@example.com")
    db.add(
        Workspace(
            name="old", slug="old", created_by=other.id,
            created_at=NOW - timedelta(hours=2),
        )
    )
    await db.flush()
    user = await _user(db)
    ws = await create_self_serve(db, user, "Mine", now=NOW)
    assert ws.id


@pytest.mark.asyncio
async def test_caller_slug_is_used_verbatim(db):
    user = await _user(db)
    ws = await create_self_serve(db, user, "Mine", slug="my-slug", now=NOW)
    assert ws.slug == "my-slug"


@pytest.mark.asyncio
async def test_caller_slug_collision_is_value_error(db):
    a = await _user(db, "a@example.com")
    b = await _user(db, "b@example.com")
    await create_self_serve(db, a, "A", slug="taken", now=NOW)
    with pytest.raises(ValueError):
        await create_self_serve(db, b, "B", slug="taken", now=NOW)


@pytest.mark.asyncio
async def test_list_admin_workspaces_filters_role(db):
    user = await _user(db)
    ws = await create_self_serve(db, user, "Mine", now=NOW)  # owner
    viewer_ws = Workspace(name="v", slug="v", created_by=None)
    db.add(viewer_ws)
    await db.flush()
    db.add(WorkspaceMembership(workspace_id=viewer_ws.id, user_id=user.id, role="viewer"))
    await db.commit()
    rows = await workspace_service.list_admin_workspaces(db, user.id)
    assert [(w.id, r) for w, r in rows] == [(ws.id, "owner")]
```

Run: `cd service && uv run pytest tests/test_self_serve_workspace.py -v` — Expected: ImportError on `SelfServeCapReached`.

- [ ] **Step 2: Implement in `workspace_service.py`**

Add imports at the top:

```python
import re
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select

from src.config import settings
```

(keep the existing imports; `delete, select` already imported — merge into one line.)

Add after `create_workspace`:

```python
class SelfServeDisabled(Exception):
    """SELF_SERVE_ENABLED is off."""


class SelfServeCapReached(Exception):
    """The user already created SELF_SERVE_MAX_WORKSPACES_PER_USER workspaces."""


class SelfServeThrottled(Exception):
    """Instance-wide SELF_SERVE_MAX_CREATES_PER_HOUR reached."""


_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """lowercase; runs of non-[a-z0-9] -> '-'; trimmed; <= 40 chars; 'ws' if empty."""
    base = _NON_SLUG.sub("-", name.lower()).strip("-")[:40].strip("-")
    return base or "ws"


async def count_created_by(db: AsyncSession, user_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(Workspace).where(Workspace.created_by == user_id)
    return int(await db.scalar(stmt) or 0)


async def get_member_role(
    db: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID
) -> str | None:
    stmt = select(WorkspaceMembership.role).where(
        WorkspaceMembership.workspace_id == workspace_id,
        WorkspaceMembership.user_id == user_id,
    )
    return await db.scalar(stmt)


async def list_admin_workspaces(
    db: AsyncSession, user_id: uuid.UUID
) -> list[tuple[Workspace, str]]:
    """Workspaces where the user is owner or admin, with the role."""
    stmt = (
        select(Workspace, WorkspaceMembership.role)
        .join(WorkspaceMembership)
        .where(
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.role.in_(("owner", "admin")),
        )
        .order_by(Workspace.created_at)
    )
    return [(ws, role) for ws, role in (await db.execute(stmt)).all()]


async def create_self_serve(
    db: AsyncSession,
    user: User,
    name: str,
    *,
    slug: str | None = None,
    now: datetime | None = None,
) -> Workspace:
    """Self-serve workspace creation: flag -> per-user cap -> hourly breaker -> create.

    The user row is locked FOR UPDATE for the duration so concurrent requests
    from one user cannot both pass the cap (no-op on SQLite). The breaker counts
    *successful* creations instance-wide, so unauthenticated traffic cannot
    exhaust it. ``slug=None`` generates ``slugify(name)-<4 hex>``; a caller-supplied
    slug (proxy-mode API) is used verbatim and a collision is a ValueError.
    """
    if not settings.self_serve_enabled:
        raise SelfServeDisabled()
    now = now or datetime.now(UTC)
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())
    if await count_created_by(db, user.id) >= settings.self_serve_max_workspaces_per_user:
        raise SelfServeCapReached()
    recent = await db.scalar(
        select(func.count())
        .select_from(Workspace)
        .where(Workspace.created_at > now - timedelta(hours=1))
    )
    if int(recent or 0) >= settings.self_serve_max_creates_per_hour:
        raise SelfServeThrottled()
    if slug is not None:
        return await create_workspace(db, name=name, slug=slug, created_by=user.id)
    for _ in range(3):
        try:
            return await create_workspace(
                db,
                name=name,
                slug=f"{slugify(name)}-{secrets.token_hex(2)}",
                created_by=user.id,
            )
        except ValueError:
            # 1-in-65536 collision; the failed flush poisoned the transaction.
            # ponytail: rollback drops the FOR UPDATE lock for the retry — a
            # same-user race here is bounded by the breaker, accepted.
            await db.rollback()
    raise ValueError("Could not allocate a unique slug")
```

Also add `from src.models.workspace import Workspace, WorkspaceMembership` (already present) — no change; ensure `User` is imported (it is).

- [ ] **Step 3: Rewire `POST /workspaces`**

In `service/src/api/workspace_routes.py`, replace the body of `create_workspace` (the route) with:

```python
    if not settings.self_serve_enabled:
        raise HTTPException(
            status_code=403, detail="Workspace creation is disabled on this server"
        )
    # Flag on: the API is a self-serve create like the hosted one — same cap and
    # breaker — otherwise one workspace would unlock unlimited creation here.
    from src.models.user import User

    actor = await db.get(User, user.user_id)
    if actor is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        workspace = await workspace_service.create_self_serve(
            db, actor, body.name, slug=body.slug
        )
    except workspace_service.SelfServeCapReached:
        raise HTTPException(status_code=403, detail="Workspace limit reached")
    except workspace_service.SelfServeThrottled:
        raise HTTPException(
            status_code=429,
            detail="Too many workspaces are being created right now",
            headers={"Retry-After": "3600"},
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if body.description:
        workspace.description = body.description
    await activity_service.log_activity(
        db,
        action="workspace_created",
        target_type="workspace",
        target_id=workspace.id,
        actor_id=user.user_id,
        workspace_id=workspace.id,
        detail={"name": workspace.name, "slug": workspace.slug, "self_serve": True},
    )
    await db.commit()
    return workspace
```

Move the `from src.models.user import User` to the module imports.

- [ ] **Step 4: Fix the existing route tests**

`service/tests/test_workspace_audit_events.py` — the create test currently monkeypatches `workspace_service.create_workspace`; change it to patch `create_self_serve` and make its `_FakeDB` return a user from `get`:

```python
    async def _create(_db, _actor, name, slug=None):
        return _workspace_ns(ws_id=new_id)

    monkeypatch.setattr(workspace_routes.workspace_service, "create_self_serve", _create)
```

and add to `_FakeDB`:

```python
    async def get(self, model, pk):
        return SimpleNamespace(id=pk)
```

In `service/tests/test_self_serve_gates.py` add the same `get` to its `_FakeDB` and two tests:

```python
def test_create_workspace_cap_maps_to_403(monkeypatch):
    monkeypatch.setattr(settings, "self_serve_enabled", True)
    from src.api import workspace_routes

    async def _boom(*a, **kw):
        raise workspace_routes.workspace_service.SelfServeCapReached()

    monkeypatch.setattr(workspace_routes.workspace_service, "create_self_serve", _boom)
    resp = _client().post("/workspaces", json={"name": "Acme", "slug": "acme"})
    assert resp.status_code == 403


def test_create_workspace_throttled_maps_to_429(monkeypatch):
    monkeypatch.setattr(settings, "self_serve_enabled", True)
    from src.api import workspace_routes

    async def _boom(*a, **kw):
        raise workspace_routes.workspace_service.SelfServeThrottled()

    monkeypatch.setattr(workspace_routes.workspace_service, "create_self_serve", _boom)
    resp = _client().post("/workspaces", json={"name": "Acme", "slug": "acme"})
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "3600"
```

- [ ] **Step 5: Run tests**

Run: `cd service && uv run pytest tests/test_self_serve_workspace.py tests/test_self_serve_gates.py tests/test_workspace_audit_events.py -v`
Expected: all PASS.

- [ ] **Step 6: Lint and commit**

```bash
make lint
git add service/src/services/workspace_service.py service/src/api/workspace_routes.py service/tests/test_self_serve_workspace.py service/tests/test_self_serve_gates.py service/tests/test_workspace_audit_events.py
git commit -m "feat(self-serve): create_self_serve (cap, hourly breaker, generated slug); POST /workspaces routes through it"
```

---

### Task 5: `invitation_service`

Implements spec §3 (`invitation_service.py`) and the redeem invariants in the threat model.

**Files:**
- Create: `service/src/services/invitation_service.py`
- Create: `service/tests/test_invitation_service.py`

**Interfaces:**
- Consumes: `workspace_service.SelfServeDisabled`, `organization_service.assert_user_allowed_in_workspace`, `WorkspaceInvitation`.
- Produces:
  - `INVITATION_TTL = timedelta(days=7)`, `MAX_PENDING_PER_WORKSPACE = 50`
  - `class InvitationInvalid(Exception)`
  - `async create(db, *, workspace_id, role, created_by, actor_role, email=None, now=None) -> tuple[WorkspaceInvitation, str]`
  - `async list_for_workspace(db, workspace_id, now=None) -> list[WorkspaceInvitation]`
  - `async revoke(db, invitation_id, *, actor_id, now=None) -> WorkspaceInvitation` (raises `InvitationInvalid` if not pending/unknown, `PermissionError` if actor is not owner/admin there)
  - `async peek(db, code, now=None) -> WorkspaceInvitation | None`
  - `async redeem(db, user, code, now=None) -> WorkspaceInvitation` (raises `InvitationInvalid`; `ValueError` from the org gate propagates)

- [ ] **Step 1: Write the failing tests**

`service/tests/test_invitation_service.py`:

```python
"""invitation_service: single-use claim, email lock, inviter standing, org gate, caps."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models.invitation import WorkspaceInvitation
from src.models.organization import Organization, WorkspaceAllowedOrganization
from src.models.user import User
from src.models.workspace import Workspace, WorkspaceMembership
from src.services import invitation_service as svc
from src.services.workspace_service import SelfServeDisabled
from tests.self_serve_fixtures import make_engine

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
PUBLIC_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest_asyncio.fixture
async def db():
    engine = await make_engine()
    async with AsyncSession(engine) as session:
        session.add(Organization(id=PUBLIC_ORG_ID, slug="public", name="Public", is_public=True, enabled=True))
        await session.commit()
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    monkeypatch.setattr(settings, "self_serve_enabled", True)


async def _user(db, email) -> User:
    u = User(email=email, name=email.split("@")[0], organization_id=PUBLIC_ORG_ID)
    db.add(u)
    await db.flush()
    return u


async def _workspace(db, owner: User, admin_role="owner") -> Workspace:
    ws = Workspace(name="Acme", slug=f"acme-{uuid.uuid4().hex[:4]}", created_by=owner.id)
    db.add(ws)
    await db.flush()
    db.add(WorkspaceMembership(workspace_id=ws.id, user_id=owner.id, role=admin_role))
    await db.commit()
    return ws


async def _role(db, ws, user):
    return await db.scalar(
        select(WorkspaceMembership.role).where(
            WorkspaceMembership.workspace_id == ws.id, WorkspaceMembership.user_id == user.id
        )
    )


@pytest.mark.asyncio
async def test_create_returns_code_once_and_stores_only_hash(db):
    owner = await _user(db, "o@example.com")
    ws = await _workspace(db, owner)
    inv, code = await svc.create(db, workspace_id=ws.id, role="editor", created_by=owner.id, actor_role="owner", now=NOW)
    assert inv.code_hash == hashlib.sha256(code.encode()).hexdigest()
    assert code not in inv.code_hash and len(code) >= 43
    assert inv.expires_at == NOW + svc.INVITATION_TTL
    assert inv.email is None


@pytest.mark.asyncio
async def test_create_rejections(db):
    owner = await _user(db, "o@example.com")
    ws = await _workspace(db, owner)
    with pytest.raises(ValueError):
        await svc.create(db, workspace_id=ws.id, role="owner", created_by=owner.id, actor_role="owner", now=NOW)
    with pytest.raises(ValueError):
        await svc.create(db, workspace_id=ws.id, role="viewer", created_by=owner.id, actor_role="editor", now=NOW)
    with pytest.raises(ValueError):
        await svc.create(db, workspace_id=ws.id, role="viewer", created_by=owner.id, actor_role="owner", email="not-an-email", now=NOW)


@pytest.mark.asyncio
async def test_create_flag_off(db, monkeypatch):
    monkeypatch.setattr(settings, "self_serve_enabled", False)
    owner = await _user(db, "o@example.com")
    ws = await _workspace(db, owner)
    with pytest.raises(SelfServeDisabled):
        await svc.create(db, workspace_id=ws.id, role="viewer", created_by=owner.id, actor_role="owner", now=NOW)


@pytest.mark.asyncio
async def test_email_lock_normalized(db):
    owner = await _user(db, "o@example.com")
    ws = await _workspace(db, owner)
    inv, _ = await svc.create(db, workspace_id=ws.id, role="viewer", created_by=owner.id, actor_role="owner", email="  Alice@Example.COM ", now=NOW)
    assert inv.email == "alice@example.com"


@pytest.mark.asyncio
async def test_pending_cap(db, monkeypatch):
    monkeypatch.setattr(svc, "MAX_PENDING_PER_WORKSPACE", 2)
    owner = await _user(db, "o@example.com")
    ws = await _workspace(db, owner)
    for _ in range(2):
        await svc.create(db, workspace_id=ws.id, role="viewer", created_by=owner.id, actor_role="owner", now=NOW)
    with pytest.raises(ValueError):
        await svc.create(db, workspace_id=ws.id, role="viewer", created_by=owner.id, actor_role="owner", now=NOW)


@pytest.mark.asyncio
async def test_redeem_happy_path_and_single_use(db):
    owner = await _user(db, "o@example.com")
    ws = await _workspace(db, owner)
    invitee = await _user(db, "i@example.com")
    _, code = await svc.create(db, workspace_id=ws.id, role="editor", created_by=owner.id, actor_role="owner", now=NOW)
    inv = await svc.redeem(db, invitee, code, now=NOW + timedelta(days=1))
    assert inv.workspace_id == ws.id
    assert await _role(db, ws, invitee) == "editor"
    with pytest.raises(svc.InvitationInvalid):
        await svc.redeem(db, invitee, code, now=NOW + timedelta(days=1))


@pytest.mark.asyncio
async def test_redeem_generic_failures(db):
    owner = await _user(db, "o@example.com")
    ws = await _workspace(db, owner)
    invitee = await _user(db, "i@example.com")
    _, expired = await svc.create(db, workspace_id=ws.id, role="viewer", created_by=owner.id, actor_role="owner", now=NOW - timedelta(days=8))
    _, locked = await svc.create(db, workspace_id=ws.id, role="viewer", created_by=owner.id, actor_role="owner", email="someone.else@example.com", now=NOW)
    revoked_inv, revoked = await svc.create(db, workspace_id=ws.id, role="viewer", created_by=owner.id, actor_role="owner", now=NOW)
    await svc.revoke(db, revoked_inv.id, actor_id=owner.id, now=NOW)
    for code in ("no-such-code", expired, locked, revoked):
        with pytest.raises(svc.InvitationInvalid):
            await svc.redeem(db, invitee, code, now=NOW)
    assert await _role(db, ws, invitee) is None


@pytest.mark.asyncio
async def test_redeem_email_lock_matches_case_insensitively(db):
    owner = await _user(db, "o@example.com")
    ws = await _workspace(db, owner)
    invitee = await _user(db, "Alice@Example.com")
    _, code = await svc.create(db, workspace_id=ws.id, role="viewer", created_by=owner.id, actor_role="owner", email="alice@example.com", now=NOW)
    await svc.redeem(db, invitee, code, now=NOW)
    assert await _role(db, ws, invitee) == "viewer"


@pytest.mark.asyncio
async def test_redeem_dies_with_inviter_standing(db):
    owner = await _user(db, "o@example.com")
    ws = await _workspace(db, owner)
    admin = await _user(db, "a@example.com")
    db.add(WorkspaceMembership(workspace_id=ws.id, user_id=admin.id, role="admin"))
    await db.commit()
    _, code = await svc.create(db, workspace_id=ws.id, role="admin", created_by=admin.id, actor_role="admin", now=NOW)
    # Owner removes the admin (simulate remove_member's effect).
    m = await db.scalar(select(WorkspaceMembership).where(WorkspaceMembership.user_id == admin.id))
    await db.delete(m)
    await db.commit()
    with pytest.raises(svc.InvitationInvalid):
        await svc.redeem(db, admin, code, now=NOW)
    assert await _role(db, ws, admin) is None


@pytest.mark.asyncio
async def test_redeem_never_changes_existing_role(db):
    owner = await _user(db, "o@example.com")
    ws = await _workspace(db, owner)
    _, code = await svc.create(db, workspace_id=ws.id, role="viewer", created_by=owner.id, actor_role="owner", now=NOW)
    inv = await svc.redeem(db, owner, code, now=NOW)  # owner redeems own invite
    assert await _role(db, ws, owner) == "owner"
    assert await db.scalar(select(WorkspaceInvitation.accepted_by).where(WorkspaceInvitation.id == inv.id)) == owner.id


@pytest.mark.asyncio
async def test_redeem_org_gate_does_not_consume(db):
    owner = await _user(db, "o@example.com")
    ws = await _workspace(db, owner)
    other_org = Organization(slug="acme", name="Acme", enabled=True)
    db.add(other_org)
    await db.flush()
    db.add(WorkspaceAllowedOrganization(workspace_id=ws.id, organization_id=other_org.id))
    await db.commit()
    invitee = await _user(db, "i@example.com")  # public org, not allowed
    inv, code = await svc.create(db, workspace_id=ws.id, role="viewer", created_by=owner.id, actor_role="owner", now=NOW)
    with pytest.raises(ValueError):
        await svc.redeem(db, invitee, code, now=NOW)
    assert await db.scalar(select(WorkspaceInvitation.accepted_at).where(WorkspaceInvitation.id == inv.id)) is None


@pytest.mark.asyncio
async def test_peek_list_and_revoke(db):
    owner = await _user(db, "o@example.com")
    ws = await _workspace(db, owner)
    inv, code = await svc.create(db, workspace_id=ws.id, role="viewer", created_by=owner.id, actor_role="owner", now=NOW)
    assert (await svc.peek(db, code, now=NOW)).id == inv.id
    assert await svc.peek(db, "nope", now=NOW) is None
    assert [i.id for i in await svc.list_for_workspace(db, ws.id, now=NOW)] == [inv.id]
    outsider = await _user(db, "x@example.com")
    with pytest.raises(PermissionError):
        await svc.revoke(db, inv.id, actor_id=outsider.id, now=NOW)
    await svc.revoke(db, inv.id, actor_id=owner.id, now=NOW)
    assert await svc.list_for_workspace(db, ws.id, now=NOW) == []
    with pytest.raises(svc.InvitationInvalid):
        await svc.revoke(db, inv.id, actor_id=owner.id, now=NOW)
```

Run: `cd service && uv run pytest tests/test_invitation_service.py -v` — Expected: `ModuleNotFoundError`.

- [ ] **Step 2: Implement `service/src/services/invitation_service.py`**

```python
"""One-time workspace join links (self-serve mode).

A code is shown to the inviter once; only its SHA-256 is stored. Redemption is
an atomic conditional UPDATE so a code can be claimed exactly once, and the
claim also requires the inviter to STILL be owner/admin of the workspace — a
removed admin's pre-minted links are dead. Every failure mode collapses to one
generic ``InvitationInvalid`` so nothing about the invitation is disclosed.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models.invitation import WorkspaceInvitation
from src.models.user import User
from src.models.workspace import Workspace, WorkspaceMembership
from src.services import organization_service
from src.services.workspace_service import SelfServeDisabled

INVITATION_TTL = timedelta(days=7)
MAX_PENDING_PER_WORKSPACE = 50
_ROLES = ("admin", "editor", "viewer")
_ADMIN_ROLES = ("owner", "admin")


class InvitationInvalid(Exception):
    """Unknown, used, expired, revoked, wrong email, or inviter lost standing."""


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _normalize_email(value: str) -> str:
    email = value.strip().lower()
    if email.count("@") != 1 or len(email) > 254 or any(c.isspace() for c in email):
        raise ValueError("Invalid email address")
    local, domain = email.split("@")
    if not local or "." not in domain:
        raise ValueError("Invalid email address")
    return email


def _pending(now: datetime):
    I = WorkspaceInvitation
    return (I.accepted_at.is_(None), I.revoked_at.is_(None), I.expires_at > now)


def _inviter_standing():
    I, M = WorkspaceInvitation, WorkspaceMembership
    return exists(
        select(M.user_id).where(
            M.workspace_id == I.workspace_id,
            M.user_id == I.created_by,
            M.role.in_(_ADMIN_ROLES),
        )
    )


def _require_enabled() -> None:
    if not settings.self_serve_enabled:
        raise SelfServeDisabled()


async def create(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    role: str,
    created_by: uuid.UUID,
    actor_role: str,
    email: str | None = None,
    now: datetime | None = None,
) -> tuple[WorkspaceInvitation, str]:
    _require_enabled()
    if actor_role not in _ADMIN_ROLES:
        raise ValueError("Only workspace owners and admins can invite")
    if role not in _ROLES:
        raise ValueError("Invitations can grant viewer, editor, or admin only")
    locked = _normalize_email(email) if email else None
    now = now or datetime.now(UTC)
    # Serialize per-workspace so concurrent creates cannot exceed the pending cap.
    await db.execute(
        select(Workspace.id).where(Workspace.id == workspace_id).with_for_update()
    )
    pending = await db.scalar(
        select(func.count())
        .select_from(WorkspaceInvitation)
        .where(WorkspaceInvitation.workspace_id == workspace_id, *_pending(now))
    )
    if int(pending or 0) >= MAX_PENDING_PER_WORKSPACE:
        raise ValueError("Too many pending invitations for this workspace")
    code = secrets.token_urlsafe(32)
    inv = WorkspaceInvitation(
        workspace_id=workspace_id,
        code_hash=_hash(code),
        email=locked,
        role=role,
        created_by=created_by,
        expires_at=now + INVITATION_TTL,
    )
    db.add(inv)
    await db.commit()
    return inv, code


async def list_for_workspace(
    db: AsyncSession, workspace_id: uuid.UUID, now: datetime | None = None
) -> list[WorkspaceInvitation]:
    now = now or datetime.now(UTC)
    stmt = (
        select(WorkspaceInvitation)
        .where(WorkspaceInvitation.workspace_id == workspace_id, *_pending(now))
        .order_by(WorkspaceInvitation.created_at)
    )
    return list((await db.execute(stmt)).scalars().all())


async def revoke(
    db: AsyncSession,
    invitation_id: uuid.UUID,
    *,
    actor_id: uuid.UUID,
    now: datetime | None = None,
) -> WorkspaceInvitation:
    _require_enabled()
    now = now or datetime.now(UTC)
    inv = await db.get(WorkspaceInvitation, invitation_id)
    if inv is None or inv.accepted_at or inv.revoked_at or inv.expires_at <= now:
        raise InvitationInvalid()
    actor_role = await db.scalar(
        select(WorkspaceMembership.role).where(
            WorkspaceMembership.workspace_id == inv.workspace_id,
            WorkspaceMembership.user_id == actor_id,
        )
    )
    if actor_role not in _ADMIN_ROLES:
        raise PermissionError("Only workspace owners and admins can revoke")
    inv.revoked_at = now
    await db.commit()
    return inv


async def peek(
    db: AsyncSession, code: str, now: datetime | None = None
) -> WorkspaceInvitation | None:
    """Read-only lookup with the same predicates the claim uses."""
    now = now or datetime.now(UTC)
    stmt = select(WorkspaceInvitation).where(
        WorkspaceInvitation.code_hash == _hash(code),
        *_pending(now),
        _inviter_standing(),
    )
    return await db.scalar(stmt)


async def redeem(
    db: AsyncSession, user: User, code: str, now: datetime | None = None
) -> WorkspaceInvitation:
    """Claim ``code`` for ``user`` and create the membership.

    Raises ``InvitationInvalid`` (generic) or the org gate's ``ValueError``
    (about the user, safe to show; the invitation is NOT consumed).
    """
    _require_enabled()
    now = now or datetime.now(UTC)
    inv = await peek(db, code, now=now)
    if inv is None:
        raise InvitationInvalid()
    if inv.email and inv.email != user.email.strip().lower():
        raise InvitationInvalid()
    await organization_service.assert_user_allowed_in_workspace(db, user, inv.workspace_id)
    result = await db.execute(
        update(WorkspaceInvitation)
        .where(WorkspaceInvitation.id == inv.id, *_pending(now), _inviter_standing())
        .values(accepted_by=user.id, accepted_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise InvitationInvalid()
    already = await db.scalar(
        select(WorkspaceMembership.id).where(
            WorkspaceMembership.workspace_id == inv.workspace_id,
            WorkspaceMembership.user_id == user.id,
        )
    )
    if already is None:
        db.add(
            WorkspaceMembership(
                workspace_id=inv.workspace_id, user_id=user.id, role=inv.role
            )
        )
    await db.commit()
    return inv
```

- [ ] **Step 3: Run tests**

Run: `cd service && uv run pytest tests/test_invitation_service.py -v` — Expected: PASS.

- [ ] **Step 4: Lint and commit**

```bash
make lint
git add service/src/services/invitation_service.py service/tests/test_invitation_service.py
git commit -m "feat(self-serve): invitation_service — one-time links, email lock, atomic claim tied to inviter standing"
```

---

### Task 6: Factor `_idp_profile` out of the proxy and admin callbacks; `_error_page(back_href=)`

Implements spec §4 callback row ("factored out of the proxy and admin callbacks") and the `back_href` extension. Lands as its own commit with the existing login tests green.

**Files:**
- Modify: `service/src/api/auth_routes.py` (`_error_page` ~L44; proxy callback ~L283-331; admin callback ~L735-783)
- Create: `service/tests/test_idp_profile.py`

**Interfaces:**
- Produces (in `auth_routes.py`):
  - `@dataclass class IdpProfile: provider_user_id: str; email: str; name: str; avatar_url: str | None; provider_data: dict`
  - `class IdpProfileError(Exception)` with `.reason: str` and `.count_for_stuffing: bool`
  - `async _idp_profile(client, token, provider) -> IdpProfile`
  - `_profile_error_page(provider: str, reason: str, back_href: str | None = None) -> HTMLResponse`
  - `_error_page(status_code, title, message, back_href: str | None = None)`

- [ ] **Step 1: Write the failing tests**

`service/tests/test_idp_profile.py`:

```python
"""_idp_profile: one profile extractor for every browser callback (proxy, admin, onboard)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.api.auth_routes import IdpProfileError, _error_page, _idp_profile


def _github_client(emails):
    client = SimpleNamespace()
    client.get = AsyncMock(
        side_effect=[
            SimpleNamespace(json=lambda: {"id": 42, "name": "Octo", "login": "octo", "avatar_url": "https://a/x.png"}),
            SimpleNamespace(json=lambda: emails),
        ]
    )
    return client


@pytest.mark.asyncio
async def test_github_uses_primary_verified_email():
    prof = await _idp_profile(
        _github_client([{"email": "o@example.com", "primary": True, "verified": True}]),
        token={"access_token": "t"},
        provider="github",
    )
    assert prof.provider_user_id == "42"
    assert prof.email == "o@example.com"
    assert prof.name == "Octo"
    assert prof.avatar_url == "https://a/x.png"
    assert prof.provider_data["email"] == "o@example.com"


@pytest.mark.asyncio
async def test_github_unverified_primary_rejected():
    with pytest.raises(IdpProfileError) as ei:
        await _idp_profile(
            _github_client([{"email": "o@example.com", "primary": True, "verified": False}]),
            token={}, provider="github",
        )
    assert ei.value.reason == "email_not_verified"
    assert ei.value.count_for_stuffing is True


@pytest.mark.asyncio
async def test_oidc_verified():
    token = {"userinfo": {"sub": "s1", "email": "g@example.com", "email_verified": True, "name": "G", "picture": "https://p"}}
    prof = await _idp_profile(SimpleNamespace(), token=token, provider="google")
    assert (prof.provider_user_id, prof.email, prof.name, prof.avatar_url) == ("s1", "g@example.com", "G", "https://p")
    assert prof.provider_data == token["userinfo"]


@pytest.mark.asyncio
async def test_oidc_unverified_rejected():
    token = {"userinfo": {"sub": "s1", "email": "g@example.com", "email_verified": "true"}}
    with pytest.raises(IdpProfileError) as ei:
        await _idp_profile(SimpleNamespace(), token=token, provider="google")
    assert ei.value.reason == "email_not_verified"


@pytest.mark.asyncio
async def test_oidc_no_email_is_not_counted_for_stuffing(monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "entra_tenant_id", "tid")
    token = {"userinfo": {"sub": "s1", "tid": "tid"}}
    with pytest.raises(IdpProfileError) as ei:
        await _idp_profile(SimpleNamespace(), token=token, provider="entra_id")
    assert ei.value.reason == "no_email_claim"
    assert ei.value.count_for_stuffing is False


def test_error_page_back_link_is_escaped_and_optional():
    plain = _error_page(400, "T", "M").body.decode()
    assert "Back to" not in plain
    linked = _error_page(400, "T", "M", back_href='https://x/"><s>').body.decode()
    assert 'href="https://x/&quot;&gt;&lt;s&gt;"' in linked
    assert "Back to sign-in" in linked
```

Run: `cd service && uv run pytest tests/test_idp_profile.py -v` — Expected: ImportError.

- [ ] **Step 2: Implement the helper and extend `_error_page`**

In `service/src/api/auth_routes.py` add `from dataclasses import dataclass` to the imports, then above `_error_page`:

```python
@dataclass
class IdpProfile:
    provider_user_id: str
    email: str
    name: str
    avatar_url: str | None
    provider_data: dict


class IdpProfileError(Exception):
    """The IdP did not give us a verified email. ``reason`` feeds
    ``_log_login_failure``; callers render their own response."""

    def __init__(self, reason: str, *, count_for_stuffing: bool = True):
        super().__init__(reason)
        self.reason = reason
        self.count_for_stuffing = count_for_stuffing


async def _idp_profile(client, token: dict, provider: str) -> IdpProfile:
    """Extract the signed-in identity from an authlib token, provider-aware.

    GitHub: profile + /user/emails (primary AND verified only). OIDC providers:
    the ID-token claims, gated by ``is_email_verified_claim`` (strict boolean,
    Entra tenant-pin exemption) and ``extract_email_claim``.
    """
    if provider == "github":
        resp = await client.get("user", token=token)
        profile = resp.json()
        # Always validate email via /user/emails (profile email may be unverified)
        resp = await client.get("user/emails", token=token)
        primary = next(
            (e for e in resp.json() if e.get("primary") and e.get("verified")), None
        )
        if not primary:
            raise IdpProfileError("email_not_verified")
        profile["email"] = primary["email"]
        return IdpProfile(
            provider_user_id=str(profile["id"]),
            email=profile["email"],
            name=profile.get("name") or profile.get("login", ""),
            avatar_url=profile.get("avatar_url"),
            provider_data=profile,
        )
    userinfo = token.get("userinfo", {})
    if not auth_service.is_email_verified_claim(userinfo, provider):
        raise IdpProfileError("email_not_verified")
    email = auth_service.extract_email_claim(userinfo)
    if not email:
        # Misconfigured app registration, not a credential attack —
        # keep it out of the stuffing counter.
        raise IdpProfileError("no_email_claim", count_for_stuffing=False)
    return IdpProfile(
        provider_user_id=userinfo.get("sub", ""),
        email=email,
        name=userinfo.get("name", ""),
        avatar_url=userinfo.get("picture"),
        provider_data=dict(userinfo),
    )


def _profile_error_page(
    provider: str, reason: str, back_href: str | None = None
) -> HTMLResponse:
    if reason == "no_email_claim":
        return _error_page(
            403,
            "No Email Address",
            "Your identity provider did not return an email address. "
            "Ask your administrator to add the 'email' optional claim to "
            "the application registration.",
            back_href=back_href,
        )
    if provider == "github":
        return _error_page(
            403,
            "Email Not Verified",
            "Your GitHub account does not have a verified primary email. "
            "Please verify your email on GitHub and try again.",
            back_href=back_href,
        )
    return _error_page(
        403,
        "Email Not Verified",
        "Your identity provider did not confirm your email address. "
        "Please verify your email and try again.",
        back_href=back_href,
    )
```

Change `_error_page`'s signature to `def _error_page(status_code: int, title: str, message: str, back_href: str | None = None) -> HTMLResponse:` and, inside, compute:

```python
    back = (
        f'<p class="back"><a href="{html.escape(back_href, quote=True)}">Back to sign-in</a></p>'
        if back_href
        else ""
    )
```

then render `{back}` right after `<p>{safe_message}</p>` in the page, and add to the `<style>` block: `.back {{ margin-top: 1rem; }} .back a {{ color: #f43737; text-decoration: none; }}`.

- [ ] **Step 3: Use the helper in both existing callbacks**

Proxy `callback` (`/callback/{provider}`): replace everything from `# Extract user info based on provider` through the `if not email:` block (the whole `if provider == "github": ... else: ...`) with:

```python
        try:
            prof = await _idp_profile(client, token, provider)
        except IdpProfileError as e:
            await _log_login_failure(
                db, request, provider, e.reason, count_for_stuffing=e.count_for_stuffing
            )
            return _profile_error_page(provider, e.reason)
        provider_user_id, email, name, avatar_url, profile = (
            prof.provider_user_id, prof.email, prof.name, prof.avatar_url, prof.provider_data,
        )
```

`admin_callback`: replace the same span (from `if provider == "github":` through the `no_email_claim` redirect) with:

```python
        try:
            prof = await _idp_profile(client, token, provider)
        except IdpProfileError as e:
            await _log_login_failure(
                db,
                request,
                provider,
                e.reason,
                flow="admin",
                count_for_stuffing=e.count_for_stuffing,
            )
            return RedirectResponse(
                url=f"{settings.admin_url}/login?error={e.reason}", status_code=302
            )
        provider_user_id, email, name, avatar_url, profile = (
            prof.provider_user_id, prof.email, prof.name, prof.avatar_url, prof.provider_data,
        )
```

(The admin flow's downstream code keeps using `provider_user_id`, `email`, `name`, `avatar_url`, `profile` — read it and keep the variable names identical.)

- [ ] **Step 4: Run the callback-adjacent suites**

Run: `cd service && uv run pytest tests/test_idp_profile.py tests/test_email_verified_strict.py tests/test_login_failure_audit.py tests/test_auth_event_logging.py tests/test_no_raw_pii_logging.py -v`
Expected: PASS.

- [ ] **Step 5: Lint and commit**

```bash
make lint
git add service/src/api/auth_routes.py service/tests/test_idp_profile.py
git commit -m "refactor(auth): factor _idp_profile out of the proxy and admin callbacks; _error_page back link"
```

---

### Task 7: Hosted routes A — entry, login, callback, home, done, logout + templates

Implements spec §4 rows `GET /`, `GET /login/{provider}`, `GET /callback/{provider}`, `GET /home`, `GET /done`, `POST /logout`, the session rules, and the templates.

**Files:**
- Modify: `service/pyproject.toml` (add `"jinja2>=3.1"` to `dependencies`)
- Create: `service/src/api/onboard_routes.py`
- Create: `service/src/templates/onboard/base.html`, `login.html`, `home.html`, `done.html`
- Create: `service/tests/test_onboard_routes.py`

**Interfaces:**
- Consumes: `_idp_profile`, `IdpProfileError`, `_profile_error_page`, `_error_page`, `_log_login_failure` (auth_routes); `_validate_authz_redirect_uri` (authz_routes); `oauth`, `get_configured_providers`; `workspace_service.{list_user_workspaces,count_created_by,get_member_role}`; `invitation_service.peek`.
- Produces (module-level, used by Tasks 8–9): `router`, `_render(request, template, status=200, **ctx)`, `_session_user(request, db) -> User | None`, `_csrf_ok(request, token) -> bool`, `_url(path) -> str`, `_home()`, `_flash(request, msg)`, `_store_return_to(request, db, value) -> HTMLResponse | None`.

- [ ] **Step 1: Add the dependency**

In `service/pyproject.toml` `dependencies`, after `"nh3>=0.2.14",` add `"jinja2>=3.1",`. Run `cd service && uv sync` (the workspace lock already resolves 3.1.6; the image now installs it too).

- [ ] **Step 2: Write the templates**

`service/src/templates/onboard/base.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}Onboarding{% endblock %} — Duar</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { min-height: 100vh; display: flex; align-items: center; justify-content: center;
         background: #09090b; color: #e4e4e7; font-family: system-ui, -apple-system, sans-serif; padding: 1rem; }
  .card { max-width: {% block width %}440px{% endblock %}; width: 100%; border: 1px solid #27272a;
          border-radius: 0.75rem; background: #18181b; overflow: hidden; }
  .header { background: #f43737; padding: 1.25rem; text-align: center; }
  .brand { font-size: 0.625rem; font-weight: 700; letter-spacing: 0.15em; text-transform: uppercase; color: rgba(255,255,255,0.85); }
  .body { padding: 1.5rem; }
  h1 { font-size: 1.125rem; font-weight: 600; margin-bottom: 0.75rem; }
  h2 { font-size: 0.9rem; font-weight: 600; margin: 1.25rem 0 0.5rem; color: #d4d4d8; }
  p, li, td, label { font-size: 0.875rem; color: #a1a1aa; line-height: 1.6; }
  .flash { background: #3f1d1d; border: 1px solid #7f1d1d; color: #fecaca; padding: 0.6rem 0.8rem; border-radius: 0.5rem; margin-bottom: 1rem; font-size: 0.85rem; }
  .ok { background: #1a2e1a; border-color: #14532d; color: #bbf7d0; }
  .btn { display: inline-block; background: #f43737; color: #fff; border: 0; border-radius: 0.5rem;
         padding: 0.55rem 0.9rem; font-size: 0.875rem; font-weight: 600; cursor: pointer; text-decoration: none; }
  .btn.secondary { background: #27272a; color: #e4e4e7; }
  .row { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; margin: 0.4rem 0; }
  input[type=text], input[type=email], select { width: 100%; background: #09090b; color: #e4e4e7; border: 1px solid #3f3f46;
         border-radius: 0.5rem; padding: 0.5rem 0.6rem; font-size: 0.875rem; margin: 0.25rem 0 0.6rem; }
  input[readonly] { color: #fca5a5; }
  table { width: 100%; border-collapse: collapse; margin-top: 0.5rem; }
  td, th { text-align: left; padding: 0.4rem 0.3rem; border-top: 1px solid #27272a; font-size: 0.8rem; }
  .meta { font-size: 0.75rem; color: #52525b; margin-top: 1.25rem; padding-top: 0.75rem; border-top: 1px solid #27272a; display: flex; gap: 1rem; flex-wrap: wrap; }
  .meta a, .meta button { color: #a1a1aa; background: none; border: 0; font-size: 0.75rem; cursor: pointer; text-decoration: underline; }
  .card-inv { border: 1px solid #3f3f46; border-radius: 0.5rem; padding: 0.75rem; margin: 0.5rem 0; }
</style>
</head>
<body>
  <div class="card">
    <div class="header"><div class="brand">Duar</div></div>
    <div class="body">
      {% if flash %}<div class="flash{% if flash_ok %} ok{% endif %}">{{ flash }}</div>{% endif %}
      {% block content %}{% endblock %}
      {% block footer %}{% endblock %}
    </div>
  </div>
</body>
</html>
```

`service/src/templates/onboard/login.html`:

```html
{% extends "onboard/base.html" %}
{% block title %}Sign in{% endblock %}
{% block content %}
<h1>Sign in to continue</h1>
<p>Use the same account you sign in to the app with.</p>
{% if not providers %}
<p>No sign-in providers are configured on this server.</p>
{% endif %}
<div class="row">
{% for p in providers %}
  <a class="btn" href="{{ base_url }}/onboard/login/{{ p }}">
    {% if p == 'google' %}Google{% elif p == 'github' %}GitHub{% elif p == 'entra_id' %}Microsoft{% else %}{{ p }}{% endif %}
  </a>
{% endfor %}
</div>
{% endblock %}
```

`service/src/templates/onboard/home.html`:

```html
{% extends "onboard/base.html" %}
{% block title %}Workspaces{% endblock %}
{% block content %}
<h1>Hi {{ user.name or user.email }}</h1>

{% if invite_invalid %}
<div class="flash">That invite link is invalid, expired, or already used.</div>
{% endif %}

{% if invite %}
<div class="card-inv">
  {% if invite_member %}
  <p>You're already a member of <strong>{{ invite_ws.name }}</strong>.</p>
  <div class="row"><a class="btn secondary" href="{{ base_url }}/onboard/done">Continue</a></div>
  {% else %}
  <p>You've been invited to join <strong>{{ invite_ws.name }}</strong> as <strong>{{ invite.role }}</strong>.</p>
  <form method="post" action="{{ base_url }}/onboard/join">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <input type="hidden" name="code" value="{{ invite_code }}">
    <div class="row"><button class="btn" type="submit">Join {{ invite_ws.name }}</button></div>
  </form>
  {% endif %}
</div>
{% endif %}

<h2>Have an invite link or code?</h2>
<form method="post" action="{{ base_url }}/onboard/join">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <label for="code">Paste the link or the code</label>
  <input type="text" id="code" name="code" required autocomplete="off">
  <div class="row"><button class="btn secondary" type="submit">Join</button></div>
</form>

{% if workspaces %}
<h2>You're already in</h2>
<ul>
{% for ws in workspaces %}<li>{{ ws.name }}</li>{% endfor %}
</ul>
<div class="row"><a class="btn secondary" href="{{ base_url }}/onboard/done">Continue</a></div>
{% endif %}

{% if show_create_section %}
<h2>Create a workspace</h2>
{% if can_create %}
<form method="post" action="{{ base_url }}/onboard/create">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <label for="name">Workspace name</label>
  <input type="text" id="name" name="name" required maxlength="255">
  <div class="row"><button class="btn" type="submit">Create workspace</button></div>
</form>
{% else %}
<p>You've reached the limit of workspaces you can create.</p>
{% endif %}
{% endif %}
{% endblock %}
{% block footer %}
<div class="meta">
  <a href="{{ base_url }}/onboard/invites">Invite people</a>
  {% if return_to %}<a href="{{ return_to }}">Back to app</a>{% endif %}
  <form method="post" action="{{ base_url }}/onboard/logout"><input type="hidden" name="csrf" value="{{ csrf }}"><button type="submit">Sign out</button></form>
</div>
{% endblock %}
```

`service/src/templates/onboard/done.html`:

```html
{% extends "onboard/base.html" %}
{% block title %}All set{% endblock %}
{% block content %}
<h1>You're all set</h1>
{% if result and result.kind == 'joined' %}
<p>You joined <strong>{{ result.workspace }}</strong>.</p>
{% elif result and result.kind == 'created' %}
<p>You created <strong>{{ result.workspace }}</strong> and are its owner.</p>
{% endif %}
{% if return_to %}
<div class="row"><a class="btn" href="{{ return_to }}">Continue to app</a></div>
{% else %}
<p>Go back to the app and sign in — your workspace will be there.</p>
{% endif %}
{% endblock %}
{% block footer %}
<div class="meta">
  <a href="{{ base_url }}/onboard/home">Workspaces</a>
  <a href="{{ base_url }}/onboard/invites">Invite people</a>
</div>
{% endblock %}
```

- [ ] **Step 3: Write the failing route tests**

`service/tests/test_onboard_routes.py`:

```python
"""Hosted /onboard pages — part A (entry, login, callback, home, done, logout).

Real SQLite DB behind get_db; the IdP round-trip is faked by patching
``onboard_routes.oauth`` and the session cookie is forged with the same signer
starlette uses (tests.self_serve_fixtures.session_cookie).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

from src.api import onboard_routes
from src.api.onboard_routes import router as onboard_router
from src.config import settings
from src.database import get_db
from src.middleware.rate_limit import limiter
from src.models.organization import Organization
from src.models.service_app import ServiceApp
from src.models.user import User
from src.models.workspace import Workspace, WorkspaceMembership
from src.services import invitation_service
from tests.self_serve_fixtures import make_engine, session_cookie

SECRET = "test-secret"
PUBLIC_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    original = limiter.enabled
    limiter.enabled = False
    monkeypatch.setattr(settings, "self_serve_enabled", True)
    monkeypatch.setattr(settings, "base_url", "http://testserver")
    monkeypatch.setattr(settings, "session_secret_key", SECRET)
    yield
    limiter.enabled = original


@pytest_asyncio.fixture
async def db():
    engine = await make_engine()
    async with AsyncSession(engine) as session:
        session.add(Organization(id=PUBLIC_ORG_ID, slug="public", name="Public", is_public=True, enabled=True))
        await session.commit()
        yield session
    await engine.dispose()


@pytest.fixture
def client(db):
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key=SECRET, same_site="lax", max_age=600)
    app.include_router(onboard_router)

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    return TestClient(app, follow_redirects=False)


async def _user(db, email="u@example.com", active=True) -> User:
    u = User(email=email, name="U", organization_id=PUBLIC_ORG_ID, is_active=active)
    db.add(u)
    await db.commit()
    return u


def _login(client, user_id, extra=None):
    data = {"onboard_user_id": str(user_id), "onboard_csrf": "tok", **(extra or {})}
    client.cookies.set("session", session_cookie(data, SECRET))


# ── flag ──────────────────────────────────────────────────────────────


def test_everything_404_when_flag_off(client, monkeypatch):
    monkeypatch.setattr(settings, "self_serve_enabled", False)
    for path in ("/onboard", "/onboard/home", "/onboard/login/google", "/onboard/done"):
        assert client.get(path).status_code == 404, path
    assert client.post("/onboard/logout", data={"csrf": "x"}).status_code == 404


# ── entry ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_entry_prg_stores_code_and_validated_return_to(client, db):
    db.add(ServiceApp(service_name="app", key_hash="h", key_prefix="sk_x", allowed_origins=["https://app.example"], is_active=True))
    await db.commit()
    r = client.get("/onboard", params={"code": "abc", "return_to": "https://app.example/login"})
    assert r.status_code == 303 and r.headers["location"] == "http://testserver/onboard"
    with patch.object(onboard_routes, "get_configured_providers", return_value=["google", "github"]):
        page = client.get("/onboard")
    assert page.status_code == 200 and "/onboard/login/google" in page.text
    # keys survived the PRG (peek at the signed cookie the way the server does)
    from base64 import b64decode
    import json
    from itsdangerous import TimestampSigner
    raw = TimestampSigner(SECRET).unsign(client.cookies["session"])
    sess = json.loads(b64decode(raw))
    assert sess["onboard_code"] == "abc"
    assert sess["onboard_return_to"] == "https://app.example/login"


def test_entry_rejects_off_allowlist_return_to(client):
    r = client.get("/onboard", params={"return_to": "https://evil.example/"})
    assert r.status_code == 400 and "Return" in r.text
    assert "session" not in client.cookies


def test_entry_rejects_oversized_return_to(client):
    r = client.get("/onboard", params={"return_to": "https://a.example/" + "x" * 2100})
    assert r.status_code == 400


def test_entry_single_provider_redirects_straight_to_login(client):
    with patch.object(onboard_routes, "get_configured_providers", return_value=["google"]):
        r = client.get("/onboard")
    assert r.status_code == 302 and r.headers["location"].endswith("/onboard/login/google")


def test_entry_provider_param_only_when_configured(client):
    with patch.object(onboard_routes, "get_configured_providers", return_value=["google", "github"]):
        assert client.get("/onboard", params={"provider": "github"}).headers["location"].endswith("/login/github")
        assert client.get("/onboard", params={"provider": "dex"}).status_code == 200


@pytest.mark.asyncio
async def test_entry_signed_in_goes_home_and_keeps_identity(client, db):
    u = await _user(db)
    _login(client, u.id)
    r = client.get("/onboard", params={"code": "zzz"})
    assert r.status_code == 303
    r = client.get("/onboard")
    assert r.status_code == 302 and r.headers["location"].endswith("/onboard/home")


# ── login / callback ──────────────────────────────────────────────────


def test_login_unconfigured_provider(client):
    with patch.object(onboard_routes, "get_configured_providers", return_value=["google"]):
        r = client.get("/onboard/login/github")
    assert r.status_code == 400 and "Back to sign-in" in r.text


def test_login_redirects_to_idp_with_onboard_callback(client):
    from starlette.responses import RedirectResponse

    fake = SimpleNamespace(
        authorize_redirect=AsyncMock(return_value=RedirectResponse("https://idp/auth", status_code=302))
    )
    with patch.object(onboard_routes, "get_configured_providers", return_value=["google"]), \
         patch.object(onboard_routes.oauth, "create_client", return_value=fake):
        r = client.get("/onboard/login/google")
    assert r.status_code == 302
    args, kwargs = fake.authorize_redirect.await_args
    assert args[1] == "http://testserver/onboard/callback/google"


def _fake_oidc_client(userinfo):
    return SimpleNamespace(authorize_access_token=AsyncMock(return_value={"userinfo": userinfo}))


@pytest.mark.asyncio
async def test_callback_provisions_user_sets_session_and_audits(client, db):
    userinfo = {"sub": "g-1", "email": "new@example.com", "email_verified": True, "name": "New"}
    with patch.object(onboard_routes, "get_configured_providers", return_value=["google"]), \
         patch.object(onboard_routes.oauth, "create_client", return_value=_fake_oidc_client(userinfo)), \
         patch.object(onboard_routes.signal_service, "on_login_success", new_callable=AsyncMock) as sig:
        r = client.get("/onboard/callback/google")
    assert r.status_code == 302 and r.headers["location"].endswith("/onboard/home")
    sig.assert_awaited_once()
    user = await db.scalar(__import__("sqlalchemy").select(User).where(User.email == "new@example.com"))
    assert user is not None
    home = client.get("/onboard/home")
    assert home.status_code == 200 and "New" in home.text


@pytest.mark.asyncio
async def test_callback_honors_onboard_next(client, db):
    client.cookies.set("session", session_cookie({"onboard_next": "http://testserver/onboard/invites"}, SECRET))
    userinfo = {"sub": "g-2", "email": "n2@example.com", "email_verified": True, "name": "N2"}
    with patch.object(onboard_routes, "get_configured_providers", return_value=["google"]), \
         patch.object(onboard_routes.oauth, "create_client", return_value=_fake_oidc_client(userinfo)), \
         patch.object(onboard_routes.signal_service, "on_login_success", new_callable=AsyncMock):
        r = client.get("/onboard/callback/google")
    assert r.headers["location"] == "http://testserver/onboard/invites"


@pytest.mark.asyncio
async def test_callback_unverified_email_is_403_with_back_link(client, db):
    userinfo = {"sub": "g-3", "email": "x@example.com", "email_verified": False}
    with patch.object(onboard_routes, "get_configured_providers", return_value=["google"]), \
         patch.object(onboard_routes.oauth, "create_client", return_value=_fake_oidc_client(userinfo)), \
         patch.object(onboard_routes.signal_service, "on_login_failure", new_callable=AsyncMock):
        r = client.get("/onboard/callback/google")
    assert r.status_code == 403 and "Back to sign-in" in r.text


@pytest.mark.asyncio
async def test_callback_entra_requires_xms_edov(client, db, monkeypatch):
    monkeypatch.setattr(settings, "entra_tenant_id", "tid")
    userinfo = {"sub": "e-1", "email": "e@example.com", "tid": "tid", "name": "E"}
    with patch.object(onboard_routes, "get_configured_providers", return_value=["entra_id"]), \
         patch.object(onboard_routes.oauth, "create_client", return_value=_fake_oidc_client(userinfo)), \
         patch.object(onboard_routes.signal_service, "on_login_failure", new_callable=AsyncMock):
        r = client.get("/onboard/callback/entra_id")
    assert r.status_code == 403
    userinfo["xms_edov"] = True
    with patch.object(onboard_routes, "get_configured_providers", return_value=["entra_id"]), \
         patch.object(onboard_routes.oauth, "create_client", return_value=_fake_oidc_client(userinfo)), \
         patch.object(onboard_routes.signal_service, "on_login_success", new_callable=AsyncMock):
        r = client.get("/onboard/callback/entra_id")
    assert r.status_code == 302


@pytest.mark.asyncio
async def test_callback_inactive_user_403(client, db):
    await _user(db, "dead@example.com", active=False)
    userinfo = {"sub": "g-4", "email": "dead@example.com", "email_verified": True, "name": "D"}
    with patch.object(onboard_routes, "get_configured_providers", return_value=["google"]), \
         patch.object(onboard_routes.oauth, "create_client", return_value=_fake_oidc_client(userinfo)), \
         patch.object(onboard_routes.signal_service, "on_login_failure", new_callable=AsyncMock):
        r = client.get("/onboard/callback/google")
    assert r.status_code == 403


# ── home / done / logout ──────────────────────────────────────────────


def test_home_without_session_redirects(client):
    r = client.get("/onboard/home")
    assert r.status_code == 302 and r.headers["location"].endswith("/onboard")


@pytest.mark.asyncio
async def test_home_deactivated_user_is_logged_out(client, db):
    u = await _user(db, active=False)
    _login(client, u.id)
    r = client.get("/onboard/home")
    assert r.status_code == 302


@pytest.mark.asyncio
async def test_home_shows_invite_card_and_sections(client, db):
    owner = await _user(db, "o@example.com")
    ws = Workspace(name="Acme", slug="acme", created_by=owner.id)
    db.add(ws)
    await db.flush()
    db.add(WorkspaceMembership(workspace_id=ws.id, user_id=owner.id, role="owner"))
    await db.commit()
    _, code = await invitation_service.create(db, workspace_id=ws.id, role="editor", created_by=owner.id, actor_role="owner")
    u = await _user(db)
    _login(client, u.id, {"onboard_code": code})
    page = client.get("/onboard/home").text
    assert "invited to join <strong>Acme</strong>" in page and "editor" in page
    assert "Create a workspace" in page
    assert 'name="csrf" value="tok"' in page


@pytest.mark.asyncio
async def test_home_invalid_code_notice_and_cap_text(client, db, monkeypatch):
    monkeypatch.setattr(settings, "self_serve_max_workspaces_per_user", 0)
    u = await _user(db)
    _login(client, u.id, {"onboard_code": "bogus"})
    page = client.get("/onboard/home").text
    assert "invalid, expired, or already used" in page
    assert "Create a workspace" not in page


@pytest.mark.asyncio
async def test_home_already_member_shows_continue(client, db):
    owner = await _user(db, "o@example.com")
    ws = Workspace(name="Acme", slug="acme", created_by=owner.id)
    db.add(ws)
    await db.flush()
    db.add(WorkspaceMembership(workspace_id=ws.id, user_id=owner.id, role="owner"))
    await db.commit()
    _, code = await invitation_service.create(db, workspace_id=ws.id, role="viewer", created_by=owner.id, actor_role="owner")
    _login(client, owner.id, {"onboard_code": code})
    page = client.get("/onboard/home").text
    assert "already a member of <strong>Acme</strong>" in page
    assert "Join Acme" not in page


@pytest.mark.asyncio
async def test_done_and_logout(client, db):
    u = await _user(db)
    _login(client, u.id, {"onboard_result": {"kind": "joined", "workspace": "Acme"}, "onboard_return_to": "https://app.example/login"})
    page = client.get("/onboard/done")
    assert page.status_code == 200 and "You joined <strong>Acme</strong>" in page.text
    assert 'href="https://app.example/login"' in page.text
    assert client.post("/onboard/logout", data={"csrf": "wrong"}).status_code == 403
    r = client.post("/onboard/logout", data={"csrf": "tok"})
    assert r.status_code == 303
    assert client.get("/onboard/home").status_code == 302


@pytest.mark.asyncio
async def test_no_store_and_html_csp_marker_on_pages(client, db):
    u = await _user(db)
    _login(client, u.id)
    r = client.get("/onboard/home")
    assert r.headers.get("X-CSP-Override") == "html-page"  # consumed by SecurityHeadersMiddleware in the real app
```

Run: `cd service && uv run pytest tests/test_onboard_routes.py -v` — Expected: ImportError on `src.api.onboard_routes`.

- [ ] **Step 4: Implement `service/src/api/onboard_routes.py` (part A)**

```python
"""Duar-hosted self-serve onboarding pages (/onboard).

Server-rendered, zero-JavaScript pages where a user with no workspace joins one
through a one-time invitation link or creates one, and where workspace
owners/admins mint invitations. Identity comes from Duar's own OAuth client
flow (authlib code flow, like proxy mode and admin login) and lives in the
existing signed session cookie under ``onboard_*`` keys. Everything is gated
by ``SELF_SERVE_ENABLED`` (404 when off). See docs/guide/self-serve.md and
docs/superpowers/specs/2026-08-25-self-serve-workspaces-design.md.
"""

import hmac
import secrets
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import structlog
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import HTMLResponse, RedirectResponse, Response

from src.api.auth_routes import (
    IdpProfileError,
    _error_page,
    _idp_profile,
    _log_login_failure,
    _profile_error_page,
)
from src.api.authz_routes import _validate_authz_redirect_uri
from src.auth.providers import get_configured_providers, oauth
from src.config import settings
from src.database import get_db
from src.logging_events import log_security
from src.middleware.rate_limit import get_client_ip, limiter
from src.models.user import User
from src.models.workspace import Workspace
from src.schemas.validators import strip_html
from src.services import (
    activity_service,
    auth_service,
    invitation_service,
    organization_service,
    signal_service,
    workspace_service,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/onboard", tags=["onboard"], include_in_schema=False)

templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates"),
    autoescape=True,
)

# Keys GET /onboard resets. NEVER onboard_user_id / onboard_csrf, and never
# request.session.clear(): the same cookie carries in-flight proxy/admin/authz-idp
# OAuth round-trips from other tabs.
_RESET_KEYS = ("onboard_code", "onboard_return_to", "onboard_next", "onboard_flash", "onboard_result")
_MAX_RETURN_TO = 2048


def _require_enabled() -> None:
    if not settings.self_serve_enabled:
        raise HTTPException(status_code=404, detail="Not found")


def _url(path: str = "") -> str:
    return f"{settings.base_url}/onboard{path}"


def _home() -> str:
    return _url("/home")


def _render(request: Request, template: str, status: int = 200, **ctx) -> HTMLResponse:
    flash = request.session.pop("onboard_flash", None)
    resp = templates.TemplateResponse(
        request,
        f"onboard/{template}",
        {
            "base_url": settings.base_url,
            "flash": flash["text"] if isinstance(flash, dict) else flash,
            "flash_ok": bool(flash.get("ok")) if isinstance(flash, dict) else False,
            "csrf": request.session.get("onboard_csrf", ""),
            "return_to": request.session.get("onboard_return_to"),
            **ctx,
        },
        status_code=status,
    )
    resp.headers["X-CSP-Override"] = "html-page"
    return resp


def _flash(request: Request, text: str, ok: bool = False) -> None:
    request.session["onboard_flash"] = {"text": text, "ok": ok}


def _clear_onboard_session(request: Request) -> None:
    for key in [k for k in request.session if k.startswith("onboard_")]:
        del request.session[key]


async def _session_user(request: Request, db: AsyncSession) -> User | None:
    """The onboarding user, re-read every request (deactivation cuts the
    session off). Writes ``onboard_seen`` so the 10-minute cookie window slides
    (starlette re-issues the cookie only when the session is modified)."""
    raw = request.session.get("onboard_user_id")
    if not raw:
        return None
    try:
        user = await db.get(User, uuid.UUID(raw))
    except ValueError:
        user = None
    if user is None or not user.is_active:
        _clear_onboard_session(request)
        return None
    request.session["onboard_seen"] = int(time.time())
    return user


def _csrf_ok(request: Request, token: str | None) -> bool:
    expected = request.session.get("onboard_csrf")
    return bool(expected and token) and hmac.compare_digest(expected, token)


def _csrf_page() -> HTMLResponse:
    return _error_page(
        403, "Invalid Form Token", "Please reload the page and try again.", back_href=_home()
    )


async def _store_return_to(request: Request, db: AsyncSession, value: str) -> HTMLResponse | None:
    """Validate ``return_to`` against the ServiceApp origin allowlist and stash
    it; returns an error page instead of storing anything on failure."""
    if len(value) > _MAX_RETURN_TO:
        return _error_page(400, "Invalid Return URL", "The return address is too long.", back_href=_url())
    try:
        await _validate_authz_redirect_uri(db, value)
    except HTTPException:
        return _error_page(
            400,
            "Invalid Return URL",
            "The app you came from is not registered on this server.",
            back_href=_url(),
        )
    request.session["onboard_return_to"] = value
    return None


def _client_meta(request: Request) -> dict:
    return {
        "ip": get_client_ip(request),
        "user_agent": request.headers.get("user-agent", "")[:200],
    }


# ── entry / login / callback ──────────────────────────────────────────


@router.get("", response_class=HTMLResponse)
async def entry(
    request: Request,
    return_to: str | None = None,
    code: str | None = None,
    provider: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    _require_enabled()
    if return_to is not None or code is not None:
        for key in _RESET_KEYS:
            request.session.pop(key, None)
        if return_to is not None:
            err = await _store_return_to(request, db, return_to)
            if err is not None:
                return err
        if code:
            request.session["onboard_code"] = code.strip()[:128]
        # PRG: the code never persists in history / the address bar.
        return RedirectResponse(_url(), status_code=303)

    if await _session_user(request, db) is not None:
        return RedirectResponse(request.session.pop("onboard_next", None) or _home(), status_code=302)

    providers = get_configured_providers()
    target = provider if provider in providers else (providers[0] if len(providers) == 1 else None)
    if target:
        return RedirectResponse(_url(f"/login/{target}"), status_code=302)
    return _render(request, "login.html", providers=providers)


@router.get("/login/{provider}")
@limiter.limit(settings.rate_limit_auth)
async def login(provider: str, request: Request):
    _require_enabled()
    if provider not in get_configured_providers():
        return _error_page(
            400,
            "Provider Not Available",
            f"The login provider “{provider}” is not configured on this server.",
            back_href=_url(),
        )
    client = oauth.create_client(provider)
    return await client.authorize_redirect(request, _url(f"/callback/{provider}"))


@router.get("/callback/{provider}")
@limiter.limit(settings.rate_limit_auth)
async def callback(provider: str, request: Request, db: AsyncSession = Depends(get_db)):
    _require_enabled()
    back = _url()
    try:
        if provider not in get_configured_providers():
            return _error_page(400, "Provider Not Available", "This provider is not configured.", back_href=back)
        client = oauth.create_client(provider)
        token = await client.authorize_access_token(request)
        try:
            prof = await _idp_profile(client, token, provider)
        except IdpProfileError as e:
            await _log_login_failure(
                db, request, provider, e.reason, flow="onboard", count_for_stuffing=e.count_for_stuffing
            )
            return _profile_error_page(provider, e.reason, back_href=back)

        if provider == "entra_id" and prof.provider_data.get("xms_edov") is not True:
            # The tenant pin does not verify guest-account addresses; the
            # optional xms_edov claim does. Fail closed on the hosted flow.
            await _log_login_failure(
                db, request, provider, "email_domain_unverified", flow="onboard", count_for_stuffing=False
            )
            return _error_page(
                403,
                "Email Not Verified",
                "Your Microsoft account's email domain is not owner-verified (xms_edov).",
                back_href=back,
            )

        org = await organization_service.resolve_organization(db, prof.email)
        if org is None:
            await _log_login_failure(db, request, provider, "org_not_permitted", flow="onboard", email=prof.email)
            return _error_page(
                403,
                "Sign-In Not Permitted",
                "Your email domain is not associated with an organization on this "
                "server, and public sign-in is disabled. Contact your administrator.",
                back_href=back,
            )
        try:
            user = await auth_service.find_or_create_user(
                db=db,
                provider=provider,
                provider_user_id=prof.provider_user_id,
                email=prof.email,
                name=prof.name,
                organization_id=org.id,
                avatar_url=prof.avatar_url,
                provider_data=prof.provider_data,
            )
        except auth_service.CrossProviderEmailConflict:
            await _log_login_failure(db, request, provider, "cross_provider_conflict", flow="onboard", email=prof.email)
            return _error_page(
                409,
                "Email Already Used",
                "An account with this email address already exists under a "
                "different sign-in provider. Please sign in with your original "
                "provider, or contact your administrator to link accounts.",
                back_href=back,
            )
        if not user.is_active:
            await _log_login_failure(db, request, provider, "inactive_user", flow="onboard", count_for_stuffing=False)
            return _error_page(403, "Account Inactive", "This account has been deactivated.", back_href=back)

        meta = _client_meta(request)
        await activity_service.log_activity(
            db,
            action="user_login",
            target_type="user",
            target_id=user.id,
            actor_id=user.id,
            detail={"provider": provider, "flow": "onboard", **meta},
        )
        await db.commit()
        await signal_service.on_login_success(db, user_id=user.id, ip=meta["ip"], user_agent=meta["user_agent"])
        log_security("auth.login.succeeded", outcome="success", provider=provider, actor=str(user.id), flow="onboard")

        request.session["onboard_user_id"] = str(user.id)
        request.session["onboard_csrf"] = secrets.token_urlsafe(32)
        request.session["onboard_seen"] = int(time.time())
        return RedirectResponse(request.session.pop("onboard_next", None) or _home(), status_code=302)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("app.error.unhandled", category="app", error=str(e), exc_info=True)
        await _log_login_failure(db, request, provider, "callback_error", flow="onboard", error_type=type(e).__name__)
        return _error_page(500, "Authentication Failed", "Something went wrong during sign-in. Please try again.", back_href=back)


# ── home / done / logout ──────────────────────────────────────────────


@router.get("/home", response_class=HTMLResponse)
async def home(request: Request, db: AsyncSession = Depends(get_db)):
    _require_enabled()
    user = await _session_user(request, db)
    if user is None:
        return RedirectResponse(_url(), status_code=302)
    now = datetime.now(UTC)
    ctx: dict = {"invite": None, "invite_ws": None, "invite_member": False, "invite_invalid": False, "invite_code": None}
    code = request.session.get("onboard_code")
    if code:
        inv = await invitation_service.peek(db, code, now=now)
        if inv is None:
            ctx["invite_invalid"] = True
            request.session.pop("onboard_code", None)
        else:
            ctx.update(
                invite=inv,
                invite_ws=await db.get(Workspace, inv.workspace_id),
                invite_member=(await workspace_service.get_member_role(db, inv.workspace_id, user.id)) is not None,
                invite_code=code,
            )
    cap = settings.self_serve_max_workspaces_per_user
    created = await workspace_service.count_created_by(db, user.id)
    return _render(
        request,
        "home.html",
        user=user,
        workspaces=await workspace_service.list_user_workspaces(db, user.id),
        show_create_section=cap > 0,
        can_create=created < cap,
        **ctx,
    )


@router.get("/done", response_class=HTMLResponse)
async def done(request: Request, db: AsyncSession = Depends(get_db)):
    _require_enabled()
    user = await _session_user(request, db)
    if user is None:
        return RedirectResponse(_url(), status_code=302)
    result = request.session.pop("onboard_result", None)
    request.session.pop("onboard_code", None)
    return _render(request, "done.html", result=result)


@router.post("/logout")
async def logout(request: Request, csrf: str = Form(""), db: AsyncSession = Depends(get_db)):
    _require_enabled()
    if await _session_user(request, db) is None:
        return RedirectResponse(_url(), status_code=303)
    if not _csrf_ok(request, csrf):
        return _csrf_page()
    _clear_onboard_session(request)
    return RedirectResponse(_url(), status_code=303)
```

- [ ] **Step 5: Run the tests**

Run: `cd service && uv run pytest tests/test_onboard_routes.py -v` — Expected: PASS.

If `TemplateResponse(request, name, ctx, status_code=...)` raises on the installed Starlette, use the keyword form `templates.TemplateResponse(request=request, name=..., context=..., status_code=...)`.

- [ ] **Step 6: Lint and commit**

```bash
make lint
git add service/pyproject.toml uv.lock service/src/api/onboard_routes.py service/src/templates service/tests/test_onboard_routes.py
git commit -m "feat(self-serve): hosted /onboard entry, login, callback, home, done, logout"
```

---

### Task 8: Hosted routes B — `POST /join` and `POST /create`

Implements spec §4 rows `POST /join`, `POST /create` and the audit/event naming in §3.

**Files:**
- Modify: `service/src/api/onboard_routes.py`
- Create: `service/tests/test_onboard_actions.py`

**Interfaces:**
- Consumes: `invitation_service.redeem`/`InvitationInvalid`, `workspace_service.create_self_serve` + exceptions, `_session_user`, `_csrf_ok`, `_flash`, `_render`.

- [ ] **Step 1: Write the failing tests**

`service/tests/test_onboard_actions.py`:

```python
"""Hosted /onboard pages — part B (join, create)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

from src.api import onboard_routes
from src.api.onboard_routes import router as onboard_router
from src.config import settings
from src.database import get_db
from src.middleware.rate_limit import limiter
from src.models.activity import ActivityLog
from src.models.organization import Organization
from src.models.user import User
from src.models.workspace import Workspace, WorkspaceMembership
from src.services import invitation_service
from tests.self_serve_fixtures import make_engine, session_cookie

SECRET = "test-secret"
PUBLIC_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    original = limiter.enabled
    limiter.enabled = False
    monkeypatch.setattr(settings, "self_serve_enabled", True)
    monkeypatch.setattr(settings, "base_url", "http://testserver")
    monkeypatch.setattr(settings, "session_secret_key", SECRET)
    monkeypatch.setattr(settings, "self_serve_max_workspaces_per_user", 1)
    monkeypatch.setattr(settings, "self_serve_max_creates_per_hour", 30)
    yield
    limiter.enabled = original


@pytest_asyncio.fixture
async def db():
    engine = await make_engine()
    async with AsyncSession(engine) as session:
        session.add(Organization(id=PUBLIC_ORG_ID, slug="public", name="Public", is_public=True, enabled=True))
        await session.commit()
        yield session
    await engine.dispose()


@pytest.fixture
def client(db):
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key=SECRET, same_site="lax", max_age=600)
    app.include_router(onboard_router)

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    return TestClient(app, follow_redirects=False)


async def _user(db, email="u@example.com") -> User:
    u = User(email=email, name="U", organization_id=PUBLIC_ORG_ID)
    db.add(u)
    await db.commit()
    return u


async def _invite(db):
    owner = await _user(db, f"o-{uuid.uuid4().hex[:4]}@example.com")
    ws = Workspace(name="Acme", slug=f"acme-{uuid.uuid4().hex[:4]}", created_by=owner.id)
    db.add(ws)
    await db.flush()
    db.add(WorkspaceMembership(workspace_id=ws.id, user_id=owner.id, role="owner"))
    await db.commit()
    _, code = await invitation_service.create(db, workspace_id=ws.id, role="editor", created_by=owner.id, actor_role="owner")
    return ws, code


def _login(client, user_id, extra=None):
    client.cookies.set("session", session_cookie({"onboard_user_id": str(user_id), "onboard_csrf": "tok", **(extra or {})}, SECRET))


async def _actions(db):
    return [a for (a,) in (await db.execute(select(ActivityLog.action).order_by(ActivityLog.created_at))).all()]


# ── join ──────────────────────────────────────────────────────────────


def test_join_without_session_redirects_not_403(client):
    r = client.post("/onboard/join", data={"code": "x", "csrf": "whatever"})
    assert r.status_code == 303 and r.headers["location"].endswith("/onboard")


@pytest.mark.asyncio
async def test_join_wrong_csrf_403(client, db):
    u = await _user(db)
    _login(client, u.id)
    assert client.post("/onboard/join", data={"code": "x", "csrf": "nope"}).status_code == 403


@pytest.mark.asyncio
async def test_join_happy_path_accepts_full_link(client, db):
    ws, code = await _invite(db)
    u = await _user(db)
    _login(client, u.id)
    r = client.post("/onboard/join", data={"code": f"http://testserver/onboard?code={code}&return_to=x", "csrf": "tok"})
    assert r.status_code == 303 and r.headers["location"].endswith("/onboard/done")
    role = await db.scalar(select(WorkspaceMembership.role).where(WorkspaceMembership.user_id == u.id))
    assert role == "editor"
    assert "invitation_accepted" in await _actions(db)
    assert "You joined <strong>Acme</strong>" in client.get("/onboard/done").text


@pytest.mark.asyncio
async def test_join_invalid_code_flashes_generic(client, db):
    u = await _user(db)
    _login(client, u.id)
    r = client.post("/onboard/join", data={"code": "bogus", "csrf": "tok"})
    assert r.status_code == 303 and r.headers["location"].endswith("/onboard/home")
    assert "invalid, expired, or already used" in client.get("/onboard/home").text
    assert "invitation_rejected" in await _actions(db)


# ── create ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_happy_path(client, db):
    u = await _user(db)
    _login(client, u.id)
    r = client.post("/onboard/create", data={"name": "<b>My</b> Lab", "csrf": "tok"})
    assert r.status_code == 303 and r.headers["location"].endswith("/onboard/done")
    ws = await db.scalar(select(Workspace).where(Workspace.created_by == u.id))
    assert ws.name == "My Lab" and ws.slug.startswith("my-lab-")
    assert "workspace_created" in await _actions(db)
    assert "You created <strong>My Lab</strong>" in client.get("/onboard/done").text


@pytest.mark.asyncio
async def test_create_cap_flashes_and_audits(client, db):
    u = await _user(db)
    _login(client, u.id)
    client.post("/onboard/create", data={"name": "One", "csrf": "tok"})
    r = client.post("/onboard/create", data={"name": "Two", "csrf": "tok"})
    assert r.status_code == 303 and r.headers["location"].endswith("/onboard/home")
    assert "limit" in client.get("/onboard/home").text.lower()
    assert "self_serve_denied" in await _actions(db)


@pytest.mark.asyncio
async def test_create_throttled_is_429_page(client, db, monkeypatch):
    monkeypatch.setattr(settings, "self_serve_max_creates_per_hour", 0)
    u = await _user(db)
    _login(client, u.id)
    r = client.post("/onboard/create", data={"name": "One", "csrf": "tok"})
    assert r.status_code == 429 and "Too many workspaces" in r.text


@pytest.mark.asyncio
async def test_create_empty_name_flashes(client, db):
    u = await _user(db)
    _login(client, u.id)
    r = client.post("/onboard/create", data={"name": "<i></i>", "csrf": "tok"})
    assert r.status_code == 303
    assert "name" in client.get("/onboard/home").text.lower()
```

Run: `cd service && uv run pytest tests/test_onboard_actions.py -v` — Expected: 404/405 failures (routes missing).

- [ ] **Step 2: Implement the two routes** (append to `onboard_routes.py`)

```python
# ── join / create ─────────────────────────────────────────────────────


def _extract_code(value: str) -> str:
    """Accept a bare code or a full ``/onboard?code=…`` link."""
    value = value.strip()
    if "code=" in value:
        return (parse_qs(urlparse(value).query).get("code") or [""])[0].strip()
    return value


@router.post("/join")
@limiter.limit(settings.rate_limit_auth)
async def join(
    request: Request,
    code: str = Form(...),
    csrf: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    _require_enabled()
    user = await _session_user(request, db)
    if user is None:
        return RedirectResponse(_url(), status_code=303)
    if not _csrf_ok(request, csrf):
        return _csrf_page()
    code = _extract_code(code)[:256]
    try:
        inv = await invitation_service.redeem(db, user, code)
    except invitation_service.InvitationInvalid:
        log_security("invitation.rejected", outcome="denied", reason="invalid", actor=str(user.id))
        await activity_service.log_activity(
            db, action="invitation_rejected", target_type="user", target_id=user.id,
            actor_id=user.id, detail={"reason": "invalid"},
        )
        await db.commit()
        _flash(request, "This invitation is invalid, expired, or already used.")
        return RedirectResponse(_home(), status_code=303)
    except ValueError as e:  # org allowlist — about the user, safe to show
        log_security("invitation.rejected", outcome="denied", reason="org_not_allowed", actor=str(user.id))
        await activity_service.log_activity(
            db, action="invitation_rejected", target_type="user", target_id=user.id,
            actor_id=user.id, detail={"reason": "org_not_allowed"},
        )
        await db.commit()
        _flash(request, str(e))
        return RedirectResponse(_home(), status_code=303)
    ws = await db.get(Workspace, inv.workspace_id)
    await activity_service.log_activity(
        db, action="invitation_accepted", target_type="workspace", target_id=ws.id,
        actor_id=user.id, workspace_id=ws.id, detail={"role": inv.role},
    )
    await db.commit()
    log_security("invitation.accepted", outcome="success", actor=str(user.id), workspace_id=str(ws.id), role=inv.role)
    request.session.pop("onboard_code", None)
    request.session["onboard_result"] = {"kind": "joined", "workspace": ws.name}
    return RedirectResponse(_url("/done"), status_code=303)


@router.post("/create")
@limiter.limit(settings.rate_limit_auth)
async def create(
    request: Request,
    name: str = Form(...),
    csrf: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    _require_enabled()
    user = await _session_user(request, db)
    if user is None:
        return RedirectResponse(_url(), status_code=303)
    if not _csrf_ok(request, csrf):
        return _csrf_page()
    name = strip_html(name)[:255]  # SafeStr does not apply to Form() params
    if not name:
        _flash(request, "A workspace name is required.")
        return RedirectResponse(_home(), status_code=303)
    try:
        ws = await workspace_service.create_self_serve(db, user, name)
    except workspace_service.SelfServeCapReached:
        return await _deny_create(request, db, user, "cap", "You've reached the limit of workspaces you can create.")
    except workspace_service.SelfServeThrottled:
        await _deny_create(request, db, user, "throttled", None)
        return _error_page(
            429,
            "Too Many Workspaces",
            "Too many workspaces are being created right now — try again later.",
            back_href=_home(),
        )
    await activity_service.log_activity(
        db, action="workspace_created", target_type="workspace", target_id=ws.id,
        actor_id=user.id, workspace_id=ws.id,
        detail={"name": ws.name, "slug": ws.slug, "self_serve": True},
    )
    await db.commit()
    log_security("workspace.self_serve.created", outcome="success", actor=str(user.id), workspace_id=str(ws.id))
    request.session["onboard_result"] = {"kind": "created", "workspace": ws.name}
    return RedirectResponse(_url("/done"), status_code=303)


async def _deny_create(request: Request, db: AsyncSession, user: User, reason: str, flash: str | None) -> Response:
    log_security("workspace.self_serve.denied", outcome="denied", reason=reason, actor=str(user.id))
    await activity_service.log_activity(
        db, action="self_serve_denied", target_type="user", target_id=user.id,
        actor_id=user.id, detail={"reason": reason},
    )
    await db.commit()
    if flash:
        _flash(request, flash)
    return RedirectResponse(_home(), status_code=303)
```

- [ ] **Step 3: Run tests**

Run: `cd service && uv run pytest tests/test_onboard_actions.py tests/test_onboard_routes.py -v` — Expected: PASS.

- [ ] **Step 4: Lint and commit**

```bash
make lint
git add service/src/api/onboard_routes.py service/tests/test_onboard_actions.py
git commit -m "feat(self-serve): hosted join and create actions"
```

---

### Task 9: Hosted routes C — inviter page, create invitation, revoke

Implements spec §4 rows `GET /invites`, `POST /invites`, `POST /invites/{id}/revoke` and the `invites.html` template.

**Files:**
- Modify: `service/src/api/onboard_routes.py`
- Create: `service/src/templates/onboard/invites.html`
- Create: `service/tests/test_onboard_invites.py`

**Interfaces:**
- Consumes: `invitation_service.{create,list_for_workspace,revoke}`, `workspace_service.{list_admin_workspaces,get_member_role}`, `_store_return_to`.

- [ ] **Step 1: Write the template**

`service/src/templates/onboard/invites.html`:

```html
{% extends "onboard/base.html" %}
{% block title %}Invite people{% endblock %}
{% block width %}600px{% endblock %}
{% block content %}
<h1>Invite people</h1>

{% if new_invite %}
<div class="card-inv">
  <p><strong>Shown once — copy it now.</strong> Send this link to the person you're inviting.</p>
  <label for="link">Invite link</label>
  <input type="text" id="link" readonly value="{{ new_invite.link }}">
  <label for="newcode">or just the code</label>
  <input type="text" id="newcode" readonly value="{{ new_invite.code }}">
</div>
{% endif %}

{% if not admin_workspaces %}
<p>You're not an owner or admin of any workspace yet.</p>
{% else %}
<form method="get" action="{{ base_url }}/onboard/invites">
  <label for="workspace">Workspace</label>
  <select id="workspace" name="workspace">
  {% for ws, role in admin_workspaces %}
    <option value="{{ ws.id }}" {% if selected and ws.id == selected.id %}selected{% endif %}>{{ ws.name }} ({{ role }})</option>
  {% endfor %}
  </select>
  <div class="row"><button class="btn secondary" type="submit">Switch</button></div>
</form>

<h2>New invitation for {{ selected.name }}</h2>
<form method="post" action="{{ base_url }}/onboard/invites">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <input type="hidden" name="workspace_id" value="{{ selected.id }}">
  <label for="role">Role</label>
  <select id="role" name="role">
    <option value="viewer">viewer</option>
    <option value="editor">editor</option>
    <option value="admin">admin</option>
  </select>
  <label for="email">Lock to an email (optional)</label>
  <input type="email" id="email" name="email" placeholder="anyone with the link" maxlength="254">
  <div class="row"><button class="btn" type="submit">Create invite link</button></div>
</form>

<h2>Pending invitations</h2>
{% if not pending %}<p>None.</p>{% else %}
<table>
  <tr><th>Who</th><th>Role</th><th>Expires</th><th></th></tr>
  {% for inv in pending %}
  <tr>
    <td>{{ inv.email or 'anyone' }}</td>
    <td>{{ inv.role }}</td>
    <td>{{ inv.expires_at.strftime('%Y-%m-%d') }}</td>
    <td>
      <form method="post" action="{{ base_url }}/onboard/invites/{{ inv.id }}/revoke">
        <input type="hidden" name="csrf" value="{{ csrf }}">
        <button class="btn secondary" type="submit">Revoke</button>
      </form>
    </td>
  </tr>
  {% endfor %}
</table>
{% endif %}
{% endif %}
{% endblock %}
{% block footer %}
<div class="meta">
  <a href="{{ base_url }}/onboard/home">Workspaces</a>
  {% if return_to %}<a href="{{ return_to }}">Back to app</a>{% endif %}
  <form method="post" action="{{ base_url }}/onboard/logout"><input type="hidden" name="csrf" value="{{ csrf }}"><button type="submit">Sign out</button></form>
</div>
{% endblock %}
```

- [ ] **Step 2: Write the failing tests**

`service/tests/test_onboard_invites.py`:

```python
"""Hosted /onboard pages — part C (inviter side)."""

from __future__ import annotations

import hashlib
import uuid

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

from src.api.onboard_routes import router as onboard_router
from src.config import settings
from src.database import get_db
from src.middleware.rate_limit import limiter
from src.models.activity import ActivityLog
from src.models.invitation import WorkspaceInvitation
from src.models.organization import Organization
from src.models.service_app import ServiceApp
from src.models.user import User
from src.models.workspace import Workspace, WorkspaceMembership
from tests.self_serve_fixtures import make_engine, session_cookie

SECRET = "test-secret"
PUBLIC_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    original = limiter.enabled
    limiter.enabled = False
    monkeypatch.setattr(settings, "self_serve_enabled", True)
    monkeypatch.setattr(settings, "base_url", "http://testserver")
    monkeypatch.setattr(settings, "session_secret_key", SECRET)
    yield
    limiter.enabled = original


@pytest_asyncio.fixture
async def db():
    engine = await make_engine()
    async with AsyncSession(engine) as session:
        session.add(Organization(id=PUBLIC_ORG_ID, slug="public", name="Public", is_public=True, enabled=True))
        await session.commit()
        yield session
    await engine.dispose()


@pytest.fixture
def client(db):
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key=SECRET, same_site="lax", max_age=600)
    app.include_router(onboard_router)

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    return TestClient(app, follow_redirects=False)


async def _user_with_ws(db, role="owner"):
    u = User(email=f"{uuid.uuid4().hex[:6]}@example.com", name="U", organization_id=PUBLIC_ORG_ID)
    db.add(u)
    await db.flush()
    ws = Workspace(name="Acme", slug=f"acme-{uuid.uuid4().hex[:4]}", created_by=u.id)
    db.add(ws)
    await db.flush()
    db.add(WorkspaceMembership(workspace_id=ws.id, user_id=u.id, role=role))
    await db.commit()
    return u, ws


def _login(client, user_id, extra=None):
    client.cookies.set("session", session_cookie({"onboard_user_id": str(user_id), "onboard_csrf": "tok", **(extra or {})}, SECRET))


@pytest.mark.asyncio
async def test_invites_without_session_sets_next_and_redirects(client, db):
    wid = uuid.uuid4()
    r = client.get("/onboard/invites", params={"workspace": str(wid)})
    assert r.status_code == 302 and r.headers["location"].endswith("/onboard")
    from base64 import b64decode
    import json
    from itsdangerous import TimestampSigner
    sess = json.loads(b64decode(TimestampSigner(SECRET).unsign(client.cookies["session"])))
    assert sess["onboard_next"] == f"http://testserver/onboard/invites?workspace={wid}"


@pytest.mark.asyncio
async def test_invites_validates_return_to(client, db):
    u, _ = await _user_with_ws(db)
    _login(client, u.id)
    assert client.get("/onboard/invites", params={"return_to": "https://evil.example/"}).status_code == 400


@pytest.mark.asyncio
async def test_invites_lists_only_admin_workspaces(client, db):
    u, ws = await _user_with_ws(db, role="viewer")
    _login(client, u.id)
    assert "not an owner or admin" in client.get("/onboard/invites").text
    u2, ws2 = await _user_with_ws(db, role="admin")
    _login(client, u2.id)
    assert "New invitation for Acme" in client.get("/onboard/invites").text


@pytest.mark.asyncio
async def test_create_invite_shows_link_once_and_stores_hash(client, db):
    db.add(ServiceApp(service_name="app", key_hash="h", key_prefix="sk_x", allowed_origins=["https://app.example"], is_active=True))
    await db.commit()
    u, ws = await _user_with_ws(db)
    _login(client, u.id, {"onboard_return_to": "https://app.example/login"})
    r = client.post("/onboard/invites", data={"workspace_id": str(ws.id), "role": "editor", "email": " Bob@Example.com ", "csrf": "tok"})
    assert r.status_code == 303
    page = client.get("/onboard/invites").text
    assert "Shown once" in page
    link = page.split('id="link" readonly value="')[1].split('"')[0]
    assert link.startswith("http://testserver/onboard?code=") and "return_to=https%3A%2F%2Fapp.example%2Flogin" in link
    code = link.split("code=")[1].split("&")[0]
    inv = await db.scalar(select(WorkspaceInvitation))
    assert inv.code_hash == hashlib.sha256(code.encode()).hexdigest()
    assert inv.email == "bob@example.com" and inv.role == "editor"
    # second render: gone
    assert "Shown once" not in client.get("/onboard/invites").text
    assert "bob@example.com" in client.get("/onboard/invites").text
    actions = [a for (a,) in (await db.execute(select(ActivityLog.action))).all()]
    assert "invitation_created" in actions
    details = [d for (d,) in (await db.execute(select(ActivityLog.detail))).all()]
    assert all(code not in str(d) and inv.code_hash not in str(d) for d in details)


@pytest.mark.asyncio
async def test_create_invite_requires_admin_of_that_workspace(client, db):
    u, ws = await _user_with_ws(db, role="editor")
    _login(client, u.id)
    r = client.post("/onboard/invites", data={"workspace_id": str(ws.id), "role": "viewer", "csrf": "tok"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_create_invite_bad_email_flashes(client, db):
    u, ws = await _user_with_ws(db)
    _login(client, u.id)
    r = client.post("/onboard/invites", data={"workspace_id": str(ws.id), "role": "viewer", "email": "nope", "csrf": "tok"})
    assert r.status_code == 303
    assert "Invalid email" in client.get("/onboard/invites").text


@pytest.mark.asyncio
async def test_revoke(client, db):
    u, ws = await _user_with_ws(db)
    _login(client, u.id)
    client.post("/onboard/invites", data={"workspace_id": str(ws.id), "role": "viewer", "csrf": "tok"})
    inv = await db.scalar(select(WorkspaceInvitation))
    assert client.post(f"/onboard/invites/{inv.id}/revoke", data={"csrf": "bad"}).status_code == 403
    r = client.post(f"/onboard/invites/{inv.id}/revoke", data={"csrf": "tok"})
    assert r.status_code == 303
    await db.refresh(inv)
    assert inv.revoked_at is not None
    outsider, _ = await _user_with_ws(db)
    _login(client, outsider.id)
    r = client.post(f"/onboard/invites/{uuid.uuid4()}/revoke", data={"csrf": "tok"})
    assert r.status_code == 303  # unknown → flash, no disclosure
```

Run: `cd service && uv run pytest tests/test_onboard_invites.py -v` — Expected: 404/405 failures.

- [ ] **Step 3: Implement the routes** (append to `onboard_routes.py`)

```python
# ── inviter side ──────────────────────────────────────────────────────


def _invites_url(workspace_id: uuid.UUID | None) -> str:
    return _url("/invites") + (f"?workspace={workspace_id}" if workspace_id else "")


@router.get("/invites", response_class=HTMLResponse)
async def invites(
    request: Request,
    workspace: str | None = None,
    return_to: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    _require_enabled()
    try:
        wanted = uuid.UUID(workspace) if workspace else None
    except ValueError:
        wanted = None
    if return_to is not None:
        err = await _store_return_to(request, db, return_to)
        if err is not None:
            return err
    user = await _session_user(request, db)
    if user is None:
        # Only ever a value WE build from a validated uuid — never raw input.
        request.session["onboard_next"] = _invites_url(wanted)
        return RedirectResponse(_url(), status_code=302)
    admin_ws = await workspace_service.list_admin_workspaces(db, user.id)
    selected = next((ws for ws, _r in admin_ws if ws.id == wanted), admin_ws[0][0] if admin_ws else None)
    pending = await invitation_service.list_for_workspace(db, selected.id) if selected else []
    return _render(
        request,
        "invites.html",
        admin_workspaces=admin_ws,
        selected=selected,
        pending=pending,
        new_invite=request.session.pop("onboard_new_invite", None),
    )


@router.post("/invites")
@limiter.limit(settings.rate_limit_auth)
async def create_invite(
    request: Request,
    workspace_id: uuid.UUID = Form(...),
    role: str = Form("viewer"),
    email: str = Form(""),
    csrf: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    _require_enabled()
    user = await _session_user(request, db)
    if user is None:
        return RedirectResponse(_url(), status_code=303)
    if not _csrf_ok(request, csrf):
        return _csrf_page()
    actor_role = await workspace_service.get_member_role(db, workspace_id, user.id)
    if actor_role not in ("owner", "admin"):
        return _error_page(403, "Not Allowed", "Only workspace owners and admins can invite.", back_href=_invites_url(None))
    try:
        inv, code = await invitation_service.create(
            db, workspace_id=workspace_id, role=role, created_by=user.id,
            actor_role=actor_role, email=email.strip() or None,
        )
    except ValueError as e:
        _flash(request, str(e))
        return RedirectResponse(_invites_url(workspace_id), status_code=303)
    await activity_service.log_activity(
        db, action="invitation_created", target_type="workspace", target_id=workspace_id,
        actor_id=user.id, workspace_id=workspace_id, detail={"role": role, "locked": bool(inv.email)},
    )
    await db.commit()
    log_security("invitation.created", outcome="success", actor=str(user.id), workspace_id=str(workspace_id), role=role, locked=bool(inv.email))
    params = {"code": code}
    if request.session.get("onboard_return_to"):
        params["return_to"] = request.session["onboard_return_to"]
    # One-time display: PRG so a refresh does not re-POST; the next GET pops it.
    request.session["onboard_new_invite"] = {"link": f"{_url()}?{urlencode(params)}", "code": code}
    return RedirectResponse(_invites_url(workspace_id), status_code=303)


@router.post("/invites/{invitation_id}/revoke")
async def revoke_invite(
    request: Request,
    invitation_id: uuid.UUID,
    csrf: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    _require_enabled()
    user = await _session_user(request, db)
    if user is None:
        return RedirectResponse(_url(), status_code=303)
    if not _csrf_ok(request, csrf):
        return _csrf_page()
    try:
        inv = await invitation_service.revoke(db, invitation_id, actor_id=user.id)
    except (invitation_service.InvitationInvalid, PermissionError):
        _flash(request, "That invitation could not be revoked.")
        return RedirectResponse(_invites_url(None), status_code=303)
    await activity_service.log_activity(
        db, action="invitation_revoked", target_type="workspace", target_id=inv.workspace_id,
        actor_id=user.id, workspace_id=inv.workspace_id,
    )
    await db.commit()
    log_security("invitation.revoked", outcome="success", actor=str(user.id), workspace_id=str(inv.workspace_id))
    _flash(request, "Invitation revoked.", ok=True)
    return RedirectResponse(_invites_url(inv.workspace_id), status_code=303)
```

- [ ] **Step 4: Run tests**

Run: `cd service && uv run pytest tests/test_onboard_invites.py tests/test_onboard_actions.py tests/test_onboard_routes.py -v` — Expected: PASS.

- [ ] **Step 5: Lint and commit**

```bash
make lint
git add service/src/api/onboard_routes.py service/src/templates/onboard/invites.html service/tests/test_onboard_invites.py
git commit -m "feat(self-serve): hosted inviter page — create one-time links, revoke"
```

---

### Task 10: Wire it in — router registration, startup warning, admin SPA allowlists, PII guard

Implements spec §4 ("Registered in `PUBLIC_ROUTERS`"), §1 (startup warning), §3 (SPA allowlists, PII test).

**Files:**
- Modify: `service/src/main.py` (imports ~L10-20, `PUBLIC_ROUTERS` ~L200, lifespan warnings ~L185)
- Modify: `service/tests/test_app_tiers.py`
- Modify: `admin/src/pages/Activity.tsx:52-80`, `admin/src/components/charts.tsx:204-212`
- Modify: `service/tests/test_no_raw_pii_logging.py`

- [ ] **Step 1: Write the failing tests**

`service/tests/test_app_tiers.py` — add `"/onboard"` to the `test_all_tier_is_todays_app_superset` prefix tuple, and in the internal-tier test add `assert not _has_prefix(app, "/onboard")`, and in the public-tier test (the one asserting `/auth` is present) add `assert _has_prefix(app, "/onboard")`.

`service/tests/test_no_raw_pii_logging.py` — append:

```python
def test_onboard_routes_never_log_the_code():
    text = (SRC / "api" / "onboard_routes.py").read_text()
    # The routes never touch the hash, and no log_security / activity-detail
    # line carries the bearer code (same source-grep style as the guard above).
    assert "code_hash" not in text
    for line in text.splitlines():
        if "log_security(" in line or "detail={" in line:
            assert "code" not in line.replace("workspace_id", ""), line
```

Run: `cd service && uv run pytest tests/test_app_tiers.py tests/test_no_raw_pii_logging.py -v` — Expected: tier tests FAIL.

- [ ] **Step 2: Register the router and the startup warning**

`service/src/main.py`: add `from src.api.onboard_routes import router as onboard_router` next to the other router imports and `onboard_router,` to `PUBLIC_ROUTERS`. In the lifespan, next to the other `app.config.insecure` warnings:

```python
    from src.auth.providers import get_configured_providers

    if settings.self_serve_enabled and not get_configured_providers():
        logger.warning(
            "app.config.self_serve.no_providers",
            category="app",
            reason="SELF_SERVE_ENABLED is on but no IdP client is configured; /onboard is a dead end",
        )
```

- [ ] **Step 3: Admin SPA allowlists**

`admin/src/pages/Activity.tsx` — in the `actions` array, after `"member_invited", "member_role_changed", "member_removed",` add:

```ts
    "invitation_created", "invitation_accepted", "invitation_revoked", "invitation_rejected",
    "self_serve_denied",
```

`admin/src/components/charts.tsx` — change the two regexes:

```ts
  ["Users & members", /^(user_|member_|invitation_|batch_import|bulk_status_change|export_users)/],
  ...
  ["Workspaces & orgs", /^(workspace_|org_|self_serve_|export_workspaces)/],
```

- [ ] **Step 4: Run tests and the admin build**

Run: `cd service && uv run pytest tests/test_app_tiers.py tests/test_no_raw_pii_logging.py -v` — Expected: PASS.
Run: `cd admin && npm run build` — Expected: clean.

- [ ] **Step 5: Full suite, lint, commit**

Run: `cd service && uv run pytest -q` — Expected: all green (note the count).

```bash
make lint
git add service/src/main.py service/tests/test_app_tiers.py service/tests/test_no_raw_pii_logging.py admin/src/pages/Activity.tsx admin/src/components/charts.tsx
git commit -m "feat(self-serve): register /onboard on the public tier; startup warning; admin activity allowlists"
```

---

### Task 11: Docs and changelog

Implements spec §8.

**Files:**
- Create: `docs/guide/self-serve.md`
- Modify: `mkdocs.yml` (nav, after `Workspaces: guide/workspaces.md`)
- Modify: `docs/deployment/environment.md` (new section before `## Docker / Infrastructure`; OAuth redirect URI note under each provider)
- Modify: `docs/getting-started/configuration.md` (new `## Self-serve workspaces` section before `## Notes`)
- Modify: `docs/security.md` (after the Authentication Tiers table)
- Modify: `docs/api/resources.md:51-63`, `docs/guide/workspaces.md:79-86`
- Modify: `CHANGELOG.md` (`[Unreleased]`)

- [ ] **Step 1: Write `docs/guide/self-serve.md`**

```markdown
# Self-serve workspaces

Public-facing deployments (a demo of an app anyone can sign into with Google or
GitHub) need users with **no** workspace to get one without a Duar admin in the
loop. With `SELF_SERVE_ENABLED=true`, Duar hosts onboarding pages under
`/onboard` where a user **joins** a workspace through a one-time invitation
link (the encouraged path) or **creates** one and becomes its owner.

Everything is off by default. Consortium/enterprise deployments leave the flag
unset; the only change they see in this release is that proxy-mode
`POST /workspaces` now returns `403` (it was open to any signed-in user).

## How it works

- Pages are server-rendered by Duar (no JavaScript) and use Duar's **own**
  IdP client — the same code flow proxy mode and the admin panel use. Sign-in
  audits and security signals fire exactly as for any login.
- **Invitations** are one-time links minted by a workspace owner/admin on
  `/onboard/invites`. A link is shown once, expires after 7 days, can grant
  `viewer`, `editor`, or `admin` (never `owner`), can optionally be locked to
  one email address, and is only valid while the inviter is still an
  owner/admin of that workspace. Nobody joins a workspace without their own
  click — the legacy direct-add endpoint is disabled in self-serve mode.
- **Creation** is capped per user (`SELF_SERVE_MAX_WORKSPACES_PER_USER`,
  `0` = join-only) and instance-wide per hour
  (`SELF_SERVE_MAX_CREATES_PER_HOUR`); slugs are generated.

## Flows

**Invitee with a link** — opens `{DUAR}/onboard?code=…` → signs in → "Join
*Workspace* as *role*" → **Join** → **Continue to app** (when the link carries
a `return_to`) → signs into the app (silent; the IdP session is live).

**New user, no invite** — app's "Sign up" link → `{DUAR}/onboard?return_to=…`
→ signs in → **Create a workspace** → **Continue to app**.

**Workspace admin** — app's "Invite people" link →
`{DUAR}/onboard/invites?workspace={id}&return_to=…` → role (+ optional email
lock) → **Create invite link** → copy the link, send it however you like.
Duar does not send email.

## App integration

Two links; no SDK changes:

```html
<a href="https://duar.example/onboard?return_to=https://app.example/login">Sign up / Join a workspace</a>
<a href="https://duar.example/onboard/invites?workspace=WORKSPACE_ID&return_to=https://app.example/login">Invite people</a>
```

`return_to` must be on an origin registered in the app's Service App
`allowed_origins` (the same list AuthZ CORS uses) and should be a page that
**starts sign-in**: after onboarding the app has no session yet. In AuthZ mode
call `silentLogin('google')` with an explicit provider (the SDK forgets the
provider once a zero-workspace callback has been handled) or show a
"Sign in" button.

Show the "Sign up" link permanently on the login page and make your
`errorComponent` say "No workspace yet — sign up" for the zero-workspace error.

## Deployment checklist

1. `SELF_SERVE_ENABLED=true`; review `SELF_SERVE_MAX_WORKSPACES_PER_USER` and
   `SELF_SERVE_MAX_CREATES_PER_HOUR`.
2. An **enabled public organization** must exist (it does by default) —
   otherwise unclaimed email domains cannot sign in at all.
3. Configure Duar's own IdP client: `GOOGLE_CLIENT_ID` **and**
   `GOOGLE_CLIENT_SECRET`, and register `{BASE_URL}/onboard/callback/google`
   as a redirect URI on the same Google OAuth client the app uses.
4. Prefer Google only: GitHub accounts are cheap for bots. Entra is not
   recommended on a public instance; if used it must be single-tenant and the
   app registration must emit `xms_edov`.
5. `TIER` must include the public listener (`public` or `all`).
6. Raise `RATE_LIMIT_AUTHZ_RESOLVE` (one bucket per calling service) and put
   per-IP rate limiting at the edge; edge logs will contain single-use
   `?code=` URLs.
7. `COOKIE_SECURE=true` and a real `SESSION_SECRET_KEY`.

## Security model

Session cookie is `SameSite=Lax`, `HttpOnly`, 10-minute sliding window; every
form carries a CSRF token; pages are `Cache-Control: no-store`; `return_to` is
allowlisted; invite codes are 256-bit, stored hashed, claimed by an atomic
single-use update; invitations die with the inviter's standing; org
allowlists are enforced on redemption; every create/accept/revoke is written
to the activity log and the security event stream. Full analysis:
`docs/superpowers/specs/2026-08-25-self-serve-workspaces-design.md`.
```

- [ ] **Step 2: Nav, config docs, security, API and guide updates**

`mkdocs.yml` — after `    - Workspaces: guide/workspaces.md` add `    - Self-serve Workspaces: guide/self-serve.md`.

`docs/getting-started/configuration.md` — before `## Notes`:

```markdown
## Self-serve workspaces

Public instances only — see [Self-serve Workspaces](../guide/self-serve.md).

| Variable | Default | Description |
|----------|---------|-------------|
| `SELF_SERVE_ENABLED` | `false` | Hosts `/onboard` (join by invitation link / create a workspace). Off: `/onboard` is 404 and `POST /workspaces` is 403. |
| `SELF_SERVE_MAX_WORKSPACES_PER_USER` | `1` | Workspaces one user may create. `0` = join-only. |
| `SELF_SERVE_MAX_CREATES_PER_HOUR` | `30` | Instance-wide breaker on successful creations. |
```

`docs/deployment/environment.md` — before `## Docker / Infrastructure`:

```markdown
## Self-serve Workspaces

| Variable | Default | Required |
|----------|---------|----------|
| `SELF_SERVE_ENABLED` | `false` | No |
| `SELF_SERVE_MAX_WORKSPACES_PER_USER` | `1` | No |
| `SELF_SERVE_MAX_CREATES_PER_HOUR` | `30` | No |

With the flag on, also register `{BASE_URL}/onboard/callback/{provider}` as a
redirect URI at each IdP (next to `{BASE_URL}/auth/callback/{provider}`).
```

`docs/security.md` — after the Authentication Tiers table:

```markdown
**Self-serve mode** (`SELF_SERVE_ENABLED`, default off) adds a fifth, browser-only
surface: the hosted `/onboard` pages authenticate with the signed session cookie
set by Duar's own OAuth callback. It never grants admin access, and admin cookies
never authenticate `/onboard`. While the flag is off, `POST /workspaces` is 403.
```

`docs/api/resources.md` — change the `POST /workspaces` row to `| POST | \`/workspaces\` | any | Create workspace (caller becomes owner). Requires \`SELF_SERVE_ENABLED\`; subject to the self-serve cap and hourly breaker (403 / 429). |` and the invite row to `| POST | \`/workspaces/{id}/members/invite\` | admin | Add an existing member (403 while \`SELF_SERVE_ENABLED\` — use invitations) |`.

`docs/guide/workspaces.md` — after the invite code block add:

```markdown
!!! note "Self-serve mode"
    With `SELF_SERVE_ENABLED=true` this endpoint returns `403`: members join
    through one-time invitation links instead. See [Self-serve Workspaces](self-serve.md).
```

`CHANGELOG.md` — under `## [Unreleased]` replace the placeholder comment with:

```markdown
### Added
- **Self-serve workspaces** (`SELF_SERVE_ENABLED`, default off): Duar-hosted `/onboard` pages where a public-instance user joins a workspace through a one-time invitation link (optionally email-locked, 7-day, single-use, valid only while the inviter is still owner/admin) or creates one (per-user cap, instance-wide hourly breaker, generated slug). Inviter page at `/onboard/invites`. New table `workspace_invitations`; new activity actions `invitation_*`, `self_serve_denied`; `self_serve` block in `/admin/system/settings`. Apps integrate with two links; SDKs unchanged.

### Changed
- **Behavior change:** proxy-mode `POST /workspaces` now requires `SELF_SERVE_ENABLED` (403 otherwise). It was open to any user already holding a workspace-scoped token.
- HTML pages' CSP `form-action` is now `'self'` (was `'none'`); `/onboard` responses are `Cache-Control: no-store`.
- The proxy and admin OAuth callbacks share one IdP profile extractor (`_idp_profile`); behavior unchanged.
```

- [ ] **Step 3: Build the docs strictly and commit**

Run: `uv run --with mkdocs-material mkdocs build --strict` (or `make docs-build` if present) — Expected: no warnings.

```bash
git add docs/guide/self-serve.md mkdocs.yml docs/getting-started/configuration.md docs/deployment/environment.md docs/security.md docs/api/resources.md docs/guide/workspaces.md CHANGELOG.md
git commit -m "docs(self-serve): guide, config reference, security note, API/guide updates, changelog"
```

---

### Task 12: Manual click-through on localhost (Google SSO)

Spec §7 last bullet. One human-visible verification of the whole flow with the real IdP round-trip.

- [ ] **Step 1:** In `service/.env` set `SELF_SERVE_ENABLED=true`; ensure `GOOGLE_CLIENT_ID/SECRET` are set and `http://localhost:9003/onboard/callback/google` is a registered redirect URI on that Google client. `make start`.
- [ ] **Step 2:** Register a Service App in the admin panel with `allowed_origins` containing `http://localhost:3000` (or reuse an existing one).
- [ ] **Step 3:** Open `http://localhost:9003/onboard?return_to=http://localhost:3000/login` → sign in with Google (click-through only, per standing permission) → **Create a workspace** → `/done` shows "You created …" and a Continue link.
- [ ] **Step 4:** `/onboard/invites` → role `editor`, no email → copy the link. Open it in an incognito window → sign in with a second Google account → **Join** → `/done` shows "You joined …". Back in the first window, refresh `/onboard/invites`: the pending list is empty.
- [ ] **Step 5:** Create an email-locked invite for a wrong address; open it in incognito as the second account → generic "invalid, expired, or already used". Revoke it.
- [ ] **Step 6:** Admin panel → Activity: `workspace_created`, `invitation_created`, `invitation_accepted`, `invitation_rejected`, `invitation_revoked` rows appear with workspace labels.
- [ ] **Step 7:** Set `SELF_SERVE_ENABLED=false`, restart, confirm `/onboard` is 404. Note results in the final review handoff (no commit for this task).

---

## Self-review (done while writing)

- **Spec coverage:** §1 → T1, T10; §2 → T3; §3 → T4, T5, T8/T9 (naming); §4 rows → T7 (entry/login/callback/home/done/logout), T8 (join/create), T9 (invites/revoke), middleware → T2, router tier → T10, legacy routes → T1/T4; §5 → T11 docs; §6 → T11 + T12; §7 tests → T1–T10 (each invariant has a named test; true concurrency is Postgres-only as the spec states); §8 → T11; §9 non-goals untouched.
- **Placeholders:** none — every code step carries the code.
- **Type consistency:** `create_self_serve(db, user, name, *, slug=None, now=None)` used identically in T4 (service + route), T8; `invitation_service.create(db, *, workspace_id, role, created_by, actor_role, email=None, now=None) -> (inv, code)` in T5, T7 tests, T8 tests, T9; `redeem(db, user, code, now=None) -> WorkspaceInvitation` in T5 and T8; `revoke(db, invitation_id, *, actor_id, now=None)` in T5 and T9; `_idp_profile(client, token, provider) -> IdpProfile` in T6 and T7; `_error_page(..., back_href=)` in T6–T9; `session_cookie(data, secret)` in T3 and T7–T9.
