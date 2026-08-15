# Realm Scope Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a `Realm` (trusted app group) and an `effective_scope` so member service apps share one permission namespace and honor each other's authz tokens.

**Architecture:** A new `realms` table; service apps get a nullable `realm_id` FK (one-realm-max). A resolved `effective_scope` (= `realm.slug` for members, else the service's own `service_name`) substitutes for `service_name` in exactly three server-side spots — the scope check, the two dual-auth `svc`-claim checks — plus authz-token minting. Standalone services are byte-for-byte unchanged (their `effective_scope` *is* their `service_name`).

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, PostgreSQL 16, Redis 7, Alembic, pytest + pytest-asyncio (managed by `uv`).

## Global Constraints

- Python 3.12; run everything via `uv` (`cd service && uv run ...`).
- Tests use **pure unit style with fakes** — no real DB/Redis. Mark async tests with `@pytest.mark.asyncio`. No `conftest.py` exists; tests import from `src.*` directly (pytest `pythonpath = ["."]`).
- Action/slug naming pattern in this codebase: `^[a-z][a-z0-9-]*[a-z0-9]$` (service_name uses it; realm `slug` follows the same shape).
- Frozen dataclasses for auth contexts (`ServiceKeyContext` is `@dataclass(frozen=True)`).
- Lint/format with ruff, **changed files only**: `cd service && uv run ruff format <changed files> && uv run ruff check --fix <changed files>`. NEVER `ruff format .` or `make fmt` — they reformat unrelated **uncommitted** files in the working tree.
- **Stage only the files each task lists** (`git add <those paths>`); never `git add -A` / `git add .`.
- **Never modify** `service/src/services/role_service.py` or `service/tests/test_register_actions.py` — unrelated uncommitted user work that must stay untouched.
- Test gate = the task's **own** test file (pure unit, always runnable). The broad suite may show IdP/JWKS failures under a network-restricted sandbox — those are environmental, not task failures.
- Branch: `realm-trusted-app-group` (already checked out). Commit after every task.
- **Non-breaking invariant:** with no realm assigned, every code path must behave exactly as today. A test must prove `effective_scope == service_name` when `realm_slug is None`.

## Plan sequence (this is Plan 1 of N)

1. **Realm scope core** ← this plan (models, migration, `effective_scope`, the 3 checks, realm-scoped minting)
2. Token flows — `GET /realm/whoami`, `duar:m2m` token + `POST /realm/m2m-token`, SDK `SystemAuth`
3. Network split — `create_app(tier)`, unpublished internal listener
4. Admin — `/admin/realms` CRUD + membership, React Realms page
5. SDKs — Python + JS m2m mint/accept
6. Docs

After this plan you can: create a realm, add members programmatically, and two members share permission scope + authz-token validity (verified by unit tests).

---

### Task 1: `Realm` model + migration

**Files:**
- Create: `service/src/models/realm.py`
- Modify: `service/src/models/__init__.py`
- Modify: `service/src/models/service_app.py` (add `realm_id` FK)
- Create: `service/migrations/versions/b2c4d6e8f0a1_add_realms.py`
- Test: `service/tests/test_realm_model.py`

**Interfaces:**
- Produces: `Realm` model (`id, slug, name, m2m_ttl_s, is_active, created_by, created_at, updated_at`); `ServiceApp.realm_id: uuid.UUID | None`.

- [ ] **Step 1: Write the failing test**

```python
# service/tests/test_realm_model.py
"""The Realm model and the service_apps.realm_id membership FK."""

import uuid


def test_realm_has_expected_columns():
    from src.models.realm import Realm

    cols = {c.name for c in Realm.__table__.columns}
    assert cols == {
        "id", "slug", "name", "m2m_ttl_s", "is_active",
        "created_by", "created_at", "updated_at",
    }
    assert Realm.__table__.c.slug.unique is True


def test_realm_instance_carries_fields():
    from src.models.realm import Realm

    r = Realm(id=uuid.uuid4(), name="Acme Suite", slug="acme-suite", m2m_ttl_s=300)
    assert r.slug == "acme-suite"
    assert r.name == "Acme Suite"
    assert r.m2m_ttl_s == 300


def test_service_app_has_realm_id():
    from src.models.service_app import ServiceApp

    assert "realm_id" in ServiceApp.__table__.columns
    app = ServiceApp(
        id=uuid.uuid4(), name="Docs", service_name="docs",
        key_hash="x" * 64, key_prefix="sk_xxxx****",
        allowed_origins=[], allowed_idp_audiences=[],
    )
    assert app.realm_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_realm_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.models.realm'`.

- [ ] **Step 3: Create the `Realm` model**

```python
# service/src/models/realm.py
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.database import Base


class Realm(Base):
    """A trust group: service apps that share one permission scope + token audience.

    A member service's ``effective_scope`` becomes ``realm.slug`` (instead of its own
    ``service_name``), so all members read/write permissions and honor each other's
    authz tokens under one shared namespace.
    """

    __tablename__ = "realms"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # Lifetime (seconds) of no-user m2m tokens minted for this realm (used in Plan 2).
    m2m_ttl_s: Mapped[int] = mapped_column(
        Integer, default=300, server_default="300", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 4: Add `realm_id` FK to `ServiceApp`**

In `service/src/models/service_app.py`, add this column immediately after the `allowed_idp_audiences` block (before `last_used_at`):

```python
    # Trusted-group membership. NULL = standalone (effective_scope = service_name).
    realm_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("realms.id", ondelete="SET NULL"),
        nullable=True,
    )
```

- [ ] **Step 5: Register the model**

In `service/src/models/__init__.py`, add the import after the `service_app` import:

```python
from src.models.realm import Realm
```

and add `"Realm",` to the `__all__` list.

- [ ] **Step 6: Run test to verify it passes**

Run: `cd service && uv run pytest tests/test_realm_model.py -v`
Expected: PASS (3 passed).

- [ ] **Step 7: Write the Alembic migration**

```python
# service/migrations/versions/b2c4d6e8f0a1_add_realms.py
"""add realms table + service_apps.realm_id (trusted app groups / shared scope)

Realms group service apps into one shared permission scope + token audience. A
member's ``realm_id`` points at its realm; effective scope becomes the realm slug.
Nullable column => non-breaking (standalone apps keep their own service_name scope).

Revision ID: b2c4d6e8f0a1
Revises: f1a9c0b3d2e4
Create Date: 2026-06-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'b2c4d6e8f0a1'
down_revision: Union[str, None] = 'f1a9c0b3d2e4'
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
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.add_column(
        "service_apps",
        sa.Column("realm_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_service_apps_realm_id", "service_apps", "realms",
        ["realm_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_service_apps_realm_id", "service_apps", type_="foreignkey")
    op.drop_column("service_apps", "realm_id")
    op.drop_table("realms")
```

- [ ] **Step 8: Verify migration applies (needs the dev DB up)**

Run: `cd service && uv run alembic upgrade head`
Expected: applies `b2c4d6e8f0a1` with no error; `\d realms` shows the table. (If no DB is running, `make start` migrates on boot — defer this check to first boot and note it.)

- [ ] **Step 9: Lint + commit**

```bash
cd service && uv run ruff format $FILES && uv run ruff check --fix $FILES   # $FILES = ONLY this task's files; never 'ruff format .' / 'make fmt'
git add service/src/models/realm.py service/src/models/__init__.py \
  service/src/models/service_app.py \
  service/migrations/versions/b2c4d6e8f0a1_add_realms.py \
  service/tests/test_realm_model.py
git commit -m "feat(realm): add Realm model + service_apps.realm_id membership FK"
```

---

### Task 2: `realm_service` — create realm + manage membership

**Files:**
- Create: `service/src/services/realm_service.py`
- Test: `service/tests/test_realm_service.py`

**Interfaces:**
- Consumes: `Realm`, `ServiceApp` (Task 1); `service_app_service._invalidate_cache` (existing).
- Produces:
  - `create_realm(db, *, name: str, slug: str, m2m_ttl_s: int = 300, created_by: uuid.UUID | None = None) -> Realm`
  - `get_realm(db, realm_id: uuid.UUID) -> Realm | None`
  - `list_realms(db) -> list[Realm]`
  - `add_member(db, realm_id: uuid.UUID, service_app_id: uuid.UUID) -> ServiceApp`
  - `remove_member(db, service_app_id: uuid.UUID) -> ServiceApp`

- [ ] **Step 1: Write the failing test**

```python
# service/tests/test_realm_service.py
"""Realm creation + membership (set/clear the service_apps.realm_id FK)."""

import uuid

import pytest

from src.models.service_app import ServiceApp


class _FakeDB:
    def __init__(self, get_result=None):
        self._get = get_result
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def get(self, _model, _pk):
        return self._get

    async def flush(self):
        pass


async def _noop():
    pass


def _app() -> ServiceApp:
    return ServiceApp(
        id=uuid.uuid4(), name="Docs", service_name="docs",
        key_hash="x" * 64, key_prefix="sk_xxxx****",
        allowed_origins=[], allowed_idp_audiences=[],
    )


@pytest.mark.asyncio
async def test_create_realm_sets_fields():
    from src.services import realm_service

    realm = await realm_service.create_realm(
        _FakeDB(), name="Acme Suite", slug="acme-suite"
    )
    assert realm.slug == "acme-suite"
    assert realm.name == "Acme Suite"
    assert realm.m2m_ttl_s == 300


@pytest.mark.asyncio
async def test_add_member_sets_realm_id(monkeypatch):
    from src.services import realm_service, service_app_service

    monkeypatch.setattr(service_app_service, "_invalidate_cache", _noop)
    app = _app()
    realm_id = uuid.uuid4()
    out = await realm_service.add_member(_FakeDB(get_result=app), realm_id, app.id)
    assert out.realm_id == realm_id


@pytest.mark.asyncio
async def test_remove_member_clears_realm_id(monkeypatch):
    from src.services import realm_service, service_app_service

    monkeypatch.setattr(service_app_service, "_invalidate_cache", _noop)
    app = _app()
    app.realm_id = uuid.uuid4()
    out = await realm_service.remove_member(_FakeDB(get_result=app), app.id)
    assert out.realm_id is None


@pytest.mark.asyncio
async def test_add_member_missing_app_raises():
    from src.services import realm_service

    with pytest.raises(ValueError):
        await realm_service.add_member(_FakeDB(get_result=None), uuid.uuid4(), uuid.uuid4())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_realm_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.services.realm_service'`.

- [ ] **Step 3: Write `realm_service`**

```python
# service/src/services/realm_service.py
"""Service layer for realms (trusted app groups) + membership."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.realm import Realm
from src.models.service_app import ServiceApp


async def create_realm(
    db: AsyncSession,
    *,
    name: str,
    slug: str,
    m2m_ttl_s: int = 300,
    created_by: uuid.UUID | None = None,
) -> Realm:
    realm = Realm(
        id=uuid.uuid4(),
        name=name,
        slug=slug,
        m2m_ttl_s=m2m_ttl_s,
        created_by=created_by,
    )
    db.add(realm)
    await db.flush()
    return realm


async def get_realm(db: AsyncSession, realm_id: uuid.UUID) -> Realm | None:
    return await db.get(Realm, realm_id)


async def list_realms(db: AsyncSession) -> list[Realm]:
    result = await db.execute(select(Realm).order_by(Realm.created_at.desc()))
    return list(result.scalars().all())


async def add_member(
    db: AsyncSession, realm_id: uuid.UUID, service_app_id: uuid.UUID
) -> ServiceApp:
    """Assign a service app to a realm. The single FK enforces one-realm-max:
    re-assigning simply overwrites the prior realm. Invalidates the service-key
    cache because it stores the member's realm slug."""
    from src.services import service_app_service

    app = await db.get(ServiceApp, service_app_id)
    if not app:
        raise ValueError("Service app not found")
    app.realm_id = realm_id
    await db.flush()
    await service_app_service._invalidate_cache()
    return app


async def remove_member(db: AsyncSession, service_app_id: uuid.UUID) -> ServiceApp:
    from src.services import service_app_service

    app = await db.get(ServiceApp, service_app_id)
    if not app:
        raise ValueError("Service app not found")
    app.realm_id = None
    await db.flush()
    await service_app_service._invalidate_cache()
    return app
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd service && uv run pytest tests/test_realm_service.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Lint + commit**

```bash
cd service && uv run ruff format $FILES && uv run ruff check --fix $FILES   # $FILES = ONLY this task's files; never 'ruff format .' / 'make fmt'
git add service/src/services/realm_service.py service/tests/test_realm_service.py
git commit -m "feat(realm): realm_service create + membership add/remove"
```

---

### Task 3: `effective_scope` + realm-aware service-key validation

**Files:**
- Modify: `service/src/api/dependencies.py` (`ServiceKeyContext`, `require_service_context`)
- Modify: `service/src/services/service_app_service.py` (`validate_key`, cache encode/decode, `_rebuild_cache`)
- Test: `service/tests/test_effective_scope.py`

**Interfaces:**
- Consumes: `Realm` (Task 1).
- Produces:
  - `ServiceKeyContext.realm_slug: str | None` field + `ServiceKeyContext.effective_scope -> str` property.
  - `service_app_service.validate_key(...) -> tuple[str, uuid.UUID, str | None] | None` (now 3-tuple: `service_name, app_id, realm_slug`).
  - `service_app_service._encode_cache(service_name, app_id, realm_slug) -> str`, `_decode_cache(value) -> tuple[str, uuid.UUID, str | None]`.

- [ ] **Step 1: Write the failing test**

```python
# service/tests/test_effective_scope.py
"""effective_scope = realm slug for members, else the service's own name.
Plus the service-key cache value encode/decode (now carries the realm slug)."""

import uuid


def test_effective_scope_standalone_is_service_name():
    from src.api.dependencies import ServiceKeyContext

    ctx = ServiceKeyContext(service_name="docs")
    assert ctx.realm_slug is None
    assert ctx.effective_scope == "docs"


def test_effective_scope_member_is_realm_slug():
    from src.api.dependencies import ServiceKeyContext

    ctx = ServiceKeyContext(service_name="docs", realm_slug="acme-suite")
    assert ctx.effective_scope == "acme-suite"


def test_cache_encode_decode_roundtrip_with_realm():
    from src.services.service_app_service import _decode_cache, _encode_cache

    aid = uuid.uuid4()
    assert _decode_cache(_encode_cache("docs", aid, "acme-suite")) == (
        "docs", aid, "acme-suite",
    )


def test_cache_encode_decode_no_realm():
    from src.services.service_app_service import _decode_cache, _encode_cache

    aid = uuid.uuid4()
    assert _decode_cache(_encode_cache("docs", aid, None)) == ("docs", aid, None)


def test_cache_decode_legacy_two_part_value():
    """Pre-upgrade cache entries were 'service_name:app_id' (no realm). Must not crash."""
    from src.services.service_app_service import _decode_cache

    aid = uuid.uuid4()
    assert _decode_cache(f"docs:{aid}") == ("docs", aid, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_effective_scope.py -v`
Expected: FAIL — `TypeError: ServiceKeyContext.__init__() got an unexpected keyword argument 'realm_slug'` and `ImportError` for `_encode_cache`.

- [ ] **Step 3: Add `realm_slug` + `effective_scope` to `ServiceKeyContext`**

In `service/src/api/dependencies.py`, replace the `ServiceKeyContext` dataclass body (currently lines 20-32) with:

```python
@dataclass(frozen=True)
class ServiceKeyContext:
    """Resolved service identity from X-Service-Key header or Origin."""

    service_name: str  # bound service name, or "" in dev mode
    origin_authenticated: bool = False  # True when resolved via Origin, not service key
    # The resolved app's id. /authz/resolve uses it to lazily load the app's
    # registered IdP audience(s) for per-app token binding — only that endpoint
    # needs them, so the lookup is deferred off the hot service-auth path.
    app_id: uuid.UUID | None = None
    # Pre-resolved IdP audience(s) (OIDC client_id(s)). Normally empty here and
    # loaded lazily from ``app_id`` in /authz/resolve; tests may set it directly.
    allowed_idp_audiences: tuple[str, ...] = ()
    # The member's realm slug, if this service belongs to a realm; else None.
    realm_slug: str | None = None

    @property
    def effective_scope(self) -> str:
        """Shared scope for permission + token binding: the realm slug for a member,
        else the service's own name (standalone — today's behavior)."""
        return self.realm_slug or self.service_name
```

- [ ] **Step 4: Add cache encode/decode helpers + make `validate_key` realm-aware**

In `service/src/services/service_app_service.py`:

(a) Add an import near the top (after `from src.models.service_app import ServiceApp`):

```python
from src.models.realm import Realm
```

(b) Add these two helpers just below the `_ORIGIN_CACHE_KEY` constant:

```python
def _encode_cache(service_name: str, app_id: uuid.UUID, realm_slug: str | None) -> str:
    return f"{service_name}:{app_id}:{realm_slug or ''}"


def _decode_cache(value: str) -> tuple[str, uuid.UUID, str | None]:
    """Parse a cache value. Tolerates legacy 2-part ('svc:app_id') entries."""
    parts = value.split(":", 2)
    if len(parts) == 2:
        service_name, app_id_str = parts
        return service_name, uuid.UUID(app_id_str), None
    service_name, app_id_str, realm_slug = parts
    return service_name, uuid.UUID(app_id_str), (realm_slug or None)
```

(c) Replace `validate_key` (currently lines 57-81) with:

```python
async def validate_key(
    raw_key: str, db: AsyncSession
) -> tuple[str, uuid.UUID, str | None] | None:
    """Validate a raw API key. Returns (service_name, app_id, realm_slug) or None."""
    sha = hashlib.sha256(raw_key.encode()).hexdigest()

    r = await get_redis()
    cached = await r.hget(_CACHE_KEY, sha)
    if cached:
        svc, app_id, realm_slug = _decode_cache(cached)
        if not await _touch_last_used(db, app_id):
            return None
        return svc, app_id, realm_slug

    await _rebuild_cache(db)

    cached = await r.hget(_CACHE_KEY, sha)
    if cached:
        svc, app_id, realm_slug = _decode_cache(cached)
        if not await _touch_last_used(db, app_id):
            return None
        return svc, app_id, realm_slug

    return None
```

(d) Replace `_rebuild_cache` (currently lines 178-190) with a realm-joining version:

```python
async def _rebuild_cache(db: AsyncSession) -> None:
    """Load all active apps (+ their realm slug) into the Redis hash cache."""
    r = await get_redis()
    result = await db.execute(
        select(ServiceApp, Realm.slug)
        .outerjoin(Realm, ServiceApp.realm_id == Realm.id)
        .where(ServiceApp.is_active == True)  # noqa: E712
    )
    rows = result.all()
    pipe = r.pipeline()
    pipe.delete(_CACHE_KEY)
    for app, realm_slug in rows:
        pipe.hset(_CACHE_KEY, app.key_hash, _encode_cache(app.service_name, app.id, realm_slug))
    pipe.expire(_CACHE_KEY, _CACHE_TTL)
    await pipe.execute()
```

- [ ] **Step 5: Update `validate_key`'s two callers in `dependencies.py`**

(a) In `require_service_context` (the `if key:` block, ~lines 56-64), replace the unpack + return:

```python
        service_name, app_id, realm_slug = result
        bind_identity(request, caller_service=service_name)
        return ServiceKeyContext(
            service_name=service_name, app_id=app_id, realm_slug=realm_slug
        )
```

(b) In `get_current_user_flexible`, initialize the effective-scope holder next to `service_key_service_name` (the line `service_key_service_name: str | None = None`, ~line 285) — add right after it:

```python
    service_key_effective_scope: str | None = None
```

and update the validation block (~lines 287-294) to capture the realm slug:

```python
    if raw_key:
        from src.services import service_app_service

        result = await service_app_service.validate_key(raw_key, db)
        if result is not None:
            service_key_service_name = result[0]
            service_key_effective_scope = result[2] or result[0]
```

(The `svc`-claim comparison itself is changed in Task 4.)

- [ ] **Step 6: Run test to verify it passes**

Run: `cd service && uv run pytest tests/test_effective_scope.py -v`
Expected: PASS (5 passed).

- [ ] **Step 7: Run the full suite (catch signature-change fallout)**

Run: `cd service && uv run pytest tests/ -q`
Expected: PASS. If any test fails on `validate_key` returning a 3-tuple, it is a test that mocked `validate_key` with a 2-tuple — update that mock to return `(svc, app_id, None)`.

- [ ] **Step 8: Lint + commit**

```bash
cd service && uv run ruff format $FILES && uv run ruff check --fix $FILES   # $FILES = ONLY this task's files; never 'ruff format .' / 'make fmt'
git add service/src/api/dependencies.py service/src/services/service_app_service.py \
  service/tests/test_effective_scope.py
git commit -m "feat(realm): effective_scope + realm-aware service-key validation"
```

---

### Task 4: Honor `effective_scope` in the scope + dual-auth checks

**Files:**
- Modify: `service/src/api/dependencies.py` (`verify_service_scope`, `get_user_for_service_call`, `get_current_user_flexible`)
- Test: `service/tests/test_realm_scope_checks.py`

**Interfaces:**
- Consumes: `ServiceKeyContext.effective_scope` (Task 3); `create_authz_token` (existing).
- Produces: scope + `svc`-claim checks that compare against `effective_scope`, so a realm member's realm-scoped authz token and realm-scoped permission requests are accepted.

- [ ] **Step 1: Write the failing test**

```python
# service/tests/test_realm_scope_checks.py
"""verify_service_scope + dual-auth svc-claim check honor the realm slug."""

import uuid

import pytest
from fastapi import HTTPException

from src.api.dependencies import ServiceKeyContext, verify_service_scope


class _FakeRequest:
    def __init__(self, headers):
        self.headers = headers


def test_scope_check_accepts_realm_slug_rejects_own_name():
    ctx = ServiceKeyContext(service_name="docs", realm_slug="acme-suite")
    verify_service_scope(ctx, "acme-suite")  # realm slug is the shared scope
    with pytest.raises(HTTPException):
        verify_service_scope(ctx, "docs")  # own name is no longer the scope


def test_scope_check_standalone_unchanged():
    ctx = ServiceKeyContext(service_name="docs")
    verify_service_scope(ctx, "docs")  # standalone: scope == service_name
    with pytest.raises(HTTPException):
        verify_service_scope(ctx, "sheets")


def _realm_token(realm_slug: str) -> str:
    from src.auth.jwt import create_authz_token

    return create_authz_token(
        user_id=uuid.uuid4(),
        idp_sub="google|1",
        workspace_id=uuid.uuid4(),
        workspace_slug="w",
        workspace_role="editor",
        actions=["x"],
        service_name=realm_slug,  # minted under the REALM slug (what Plan 2 does)
        org_id=None,
        org_slug=None,
        org_is_public=False,
    )


@pytest.mark.asyncio
async def test_dual_auth_accepts_realm_scoped_token(monkeypatch):
    from src.api import dependencies as deps

    async def _noop_hygiene(_payload):
        pass

    monkeypatch.setattr(deps, "_enforce_token_hygiene", _noop_hygiene)
    monkeypatch.setattr(deps, "bind_identity", lambda *a, **k: None)

    token = _realm_token("acme-suite")
    req = _FakeRequest({"Authorization": f"Bearer {token}"})
    # Caller is "docs" but a member of realm "acme-suite":
    svc_ctx = ServiceKeyContext(service_name="docs", realm_slug="acme-suite")
    user = await deps.get_user_for_service_call(req, svc_ctx)
    assert user.workspace_role == "editor"


@pytest.mark.asyncio
async def test_dual_auth_rejects_other_realm_token(monkeypatch):
    from src.api import dependencies as deps

    async def _noop_hygiene(_payload):
        pass

    monkeypatch.setattr(deps, "_enforce_token_hygiene", _noop_hygiene)
    monkeypatch.setattr(deps, "bind_identity", lambda *a, **k: None)

    token = _realm_token("other-realm")
    req = _FakeRequest({"Authorization": f"Bearer {token}"})
    svc_ctx = ServiceKeyContext(service_name="docs", realm_slug="acme-suite")
    with pytest.raises(HTTPException) as exc:
        await deps.get_user_for_service_call(req, svc_ctx)
    assert exc.value.status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_realm_scope_checks.py -v`
Expected: FAIL — `test_scope_check_accepts_realm_slug_rejects_own_name` and `test_dual_auth_accepts_realm_scoped_token` fail because the checks still compare against `service_name`.

- [ ] **Step 3: Change `verify_service_scope` to use `effective_scope`**

In `service/src/api/dependencies.py`, replace the body of `verify_service_scope` (lines 35-41):

```python
def verify_service_scope(ctx: ServiceKeyContext, service_name: str) -> None:
    """Verify the service key is scoped to the requested service_name.

    For a realm member the authoritative scope is the realm slug (effective_scope),
    so all members share one permission namespace.
    """
    if ctx.effective_scope != service_name:
        raise HTTPException(
            status_code=403,
            detail=f"Service key is not authorized for service '{service_name}'",
        )
```

- [ ] **Step 4: Change the two `svc`-claim checks to use `effective_scope`**

(a) In `get_user_for_service_call`, the authz block (~lines 242-248):

```python
    if token_type == "authz":
        token_svc = payload.get("svc")
        if not token_svc or token_svc != svc_ctx.effective_scope:
            raise HTTPException(
                status_code=403,
                detail="Authz token was issued for a different service",
            )
```

(b) In `get_current_user_flexible`, the authz block (~lines 312-318):

```python
    if token_type == "authz":
        token_svc = payload.get("svc")
        if not token_svc or token_svc != service_key_effective_scope:
            raise HTTPException(
                status_code=403,
                detail="Authz token was issued for a different service",
            )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd service && uv run pytest tests/test_realm_scope_checks.py -v`
Expected: PASS (5 passed).

- [ ] **Step 6: Run the full suite (no regressions for standalone services)**

Run: `cd service && uv run pytest tests/ -q`
Expected: PASS — existing dual-auth/permission tests (standalone, `realm_slug is None`) still pass because `effective_scope == service_name` for them.

- [ ] **Step 7: Lint + commit**

```bash
cd service && uv run ruff format $FILES && uv run ruff check --fix $FILES   # $FILES = ONLY this task's files; never 'ruff format .' / 'make fmt'
git add service/src/api/dependencies.py service/tests/test_realm_scope_checks.py
git commit -m "feat(realm): scope + dual-auth svc checks honor effective_scope"
```

---

### Task 5: Mint realm-scoped authz tokens

**Files:**
- Modify: `service/src/api/authz_routes.py` (`/authz/resolve` handler — RBAC lookup + `create_authz_token`)
- Test: `service/tests/test_realm_authz_minting.py`

**Interfaces:**
- Consumes: `ServiceKeyContext.effective_scope` (Task 3).
- Produces: `/authz/resolve` stamps the authz token's `svc` claim and looks up RBAC actions under `effective_scope`, so a realm member receives a token every member accepts and actions resolved from the shared namespace. Audit fields (`caller_service`) keep the *real* `service_name`.

- [ ] **Step 1: Write the failing test**

**Controller note — decision overrides the sketch below.** The user chose a *behavioral* test over source-inspection. The controller will supply the concrete test in the Task 5 dispatch, modeled on the existing `service/tests/test_authz_resolve_*.py` mock scaffolding: set up a fake DB where the calling service is a realm member + the user has a workspace membership, mock `validate_idp_token`, drive `/authz/resolve`, decode the returned `authz_token`, and assert `svc == realm slug` with RBAC actions resolved under the realm scope — while audit still records the real `service_name`. The `inspect.getsource` sketch below is **superseded — do not use it**.

```python
# service/tests/test_realm_authz_minting.py
"""/authz/resolve binds the authz token + RBAC lookup to effective_scope (realm slug)."""

import inspect

from src.api import authz_routes


def test_resolve_uses_effective_scope_for_actions_and_minting():
    """Guard against regressing to service_ctx.service_name for the scope-bearing
    calls. The audit bindings (caller_service=) intentionally keep service_name."""
    src = inspect.getsource(authz_routes.authz_resolve)

    # RBAC action lookup is scoped to the realm:
    assert "get_user_actions(" in src
    assert "service_ctx.effective_scope" in src
    # The token's svc claim is the realm scope, not the per-service name:
    assert "service_name=service_ctx.effective_scope" in src
    # Audit still records the real caller service (not collapsed to the realm):
    assert "caller_service=service_ctx.service_name" in src
```

> Note: the handler function is named `authz_resolve` in `authz_routes.py`. If the symbol differs, point `inspect.getsource` at the actual resolve handler.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_realm_authz_minting.py -v`
Expected: FAIL — the source currently contains `service_name=service_ctx.service_name` and `get_user_actions(db, user.id, service_ctx.service_name, ...)`.

- [ ] **Step 3: Scope the RBAC lookup to `effective_scope`**

In `service/src/api/authz_routes.py`, the "Get RBAC actions" call (~line 370-372):

```python
    # 5. Get RBAC actions for this service (shared across the realm via effective_scope)
    actions = await get_user_actions(
        db, user.id, service_ctx.effective_scope, body.workspace_id
    )
```

- [ ] **Step 4: Stamp the authz token's `svc` with `effective_scope`**

In the same file, the `create_authz_token(...)` call (~line 390-399), change only the `service_name=` argument:

```python
        service_name=service_ctx.effective_scope,
```

Leave every `caller_service=service_ctx.service_name` (audit/logging bindings) **unchanged** — audit must record the real calling service, not the realm.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd service && uv run pytest tests/test_realm_authz_minting.py -v`
Expected: PASS (1 passed).

- [ ] **Step 6: Run the full suite**

Run: `cd service && uv run pytest tests/ -q`
Expected: PASS — standalone resolve tests still pass (`effective_scope == service_name`).

- [ ] **Step 7: Lint + commit**

```bash
cd service && uv run ruff format $FILES && uv run ruff check --fix $FILES   # $FILES = ONLY this task's files; never 'ruff format .' / 'make fmt'
git add service/src/api/authz_routes.py service/tests/test_realm_authz_minting.py
git commit -m "feat(realm): mint realm-scoped authz tokens (svc + RBAC = effective_scope)"
```

---

## Self-review (done by plan author)

**Spec coverage (Plan 1's slice):** `realms` table + `realm_id` FK → Task 1. `effective_scope` mechanism + the two surgical comparison spots → Tasks 3-4. Realm-scoped authz minting (`/authz/resolve`) → Task 5. Membership management (service layer) → Task 2. Deferred to later plans (explicitly out of this plan's scope): `whoami`, `duar:m2m` token + mint endpoint (Plan 2), network split (Plan 3), admin CRUD/UI (Plan 4), SDKs (Plan 5), docs (Plan 6).

**Placeholder scan:** none — every step carries full code/commands.

**Type consistency:** `validate_key` returns a 3-tuple `(service_name, app_id, realm_slug)` consistently in Task 3 and is unpacked as such in both callers; `ServiceKeyContext.realm_slug`/`.effective_scope` named identically across Tasks 3-5; `_encode_cache`/`_decode_cache` signatures match their tests.

**Known integration gaps (call out at execution):** the migration apply (Task 1 Step 8) and `validate_key`'s Redis path need a live DB/Redis — covered by `make start` on first boot, not by the pure-unit suite. The end-to-end "two real services share a grant over HTTP" assertion lands once Plan 2 adds `whoami` + the SDK; Plan 1 proves the mechanism at the unit level.
