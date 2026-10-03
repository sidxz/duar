# Realm Admin API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give admins a REST API to manage realms — create/list/update/delete realms and add/remove member service apps — under the existing admin-cookie auth, with audit logging and the deferred realm-slug validation.

**Architecture:** Add a `# ── Realms ──` section to the existing `admin_router` (already on the public tier, admin-cookie + `X-Requested-With` CSRF). Endpoints delegate to `realm_service` (extended with update/delete/list-members/has-grants) and log to the admin activity log (`realm_*` events). New request/response schemas live in `schemas/realm.py`; the realm slug gets a letter-start pattern. `ServiceAppResponse` gains a `realm_id` field so the UI can show membership. **This is the backend half of "admin" — the React Realms UI is a separate follow-on plan that consumes this API.**

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Pydantic v2, pytest + pytest-asyncio (managed by `uv`).

## Scope boundary (this is Plan 4 of 6 — backend half)

1. Scope core — DONE. 2. Token flows — DONE. 3. Network split — DONE (`/realm` on internal; `admin_router` is on the **public** tier, so this plan needs **NO `main.py` change**). 4. **Admin** ← this plan covers the **backend API**; the **React Realms UI** (list/detail/members pages, ServiceAppDetail realm display, routes/nav) is the immediate follow-on plan. 5. SDKs. 6. Docs.

After this plan: an admin (cookie + `X-Requested-With`) can fully manage realms over `/admin/realms*` (verified by unit + behavioral tests); the UI plan wires React on top.

## Global Constraints

- Python 3.12; run everything via `uv` (`cd service && uv run ...`).
- Tests use **pure unit style with fakes** OR the **behavioral TestClient + `dependency_overrides` + monkeypatch** house style (model admin tests on `service/tests/test_realm_routes.py` / `test_realm_authz_minting.py`, overriding `require_admin` + `get_db`). No `conftest.py`; import from `src.*` directly. Mark async unit tests `@pytest.mark.asyncio`.
- **Realm slug pattern is letter-start `^[a-z][a-z0-9-]*[a-z0-9]$`** (distinct from the existing `SLUG_PATTERN` which allows a digit start — realm slug substitutes for `service_name`, which is letter-start). Use the new `REALM_SLUG_PATTERN`.
- **Slug is immutable after creation** — changing it would re-key `effective_scope` and orphan every permission row. `RealmUpdateRequest` has NO `slug` field.
- **One-realm-max:** the single `service_apps.realm_id` FK guarantees ≤1; the add-member endpoint additionally **rejects (409)** an app already in a *different* realm (no silent steal — remove it first).
- Audit every mutation via `activity_service.log_activity(db, action="realm_*", target_type="realm", target_id=..., actor_id=..., detail={...})` — events `realm_created`, `realm_updated`, `realm_deleted`, `realm_member_added`, `realm_member_removed`. Realms are workspace-orthogonal → omit `workspace_id`.
- Lint/format with ruff, **changed files only**: `cd service && uv run ruff format <changed files> && uv run ruff check --fix <changed files>`. NEVER `ruff format .` / whole-tree `--fix` / `make fmt`.
- **Stage only the files each task lists**; never `git add -A`/`.`. **Never modify** `service/src/services/role_service.py` or `service/tests/test_register_actions.py` (the user's separate uncommitted work).
- Test gate = the task's **own** test file. Broad-suite IdP/JWKS **connection** failures are the known network-sandbox artifact.
- Branch: `realm-trusted-app-group` (already checked out). Commit after every task.

---

### Task 1: `realm_service` admin operations + `REALM_SLUG_PATTERN`

**Files:**
- Modify: `service/src/schemas/validators.py` (add `REALM_SLUG_PATTERN`)
- Modify: `service/src/services/realm_service.py` (add `update_realm`, `delete_realm`, `list_members`, `service_app_has_grants`)
- Test: `service/tests/test_realm_admin_service.py`

**Interfaces:**
- Consumes: `Realm`, `ServiceApp` (Plan 1); `service_app_service._invalidate_cache` (existing); `ServiceAction` (`src.models.role`), `ResourcePermission` (`src.models.permission`).
- Produces:
  - `REALM_SLUG_PATTERN = r"^[a-z][a-z0-9-]*[a-z0-9]$"` (validators).
  - `update_realm(db, realm_id, *, name=None, m2m_ttl_s=None, is_active=None) -> Realm | None`
  - `delete_realm(db, realm_id) -> bool`
  - `list_members(db, realm_id) -> list[ServiceApp]`
  - `service_app_has_grants(db, service_name) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# service/tests/test_realm_admin_service.py
"""Admin-facing realm_service ops: update, delete (cache-invalidating), list members,
and the has-grants check that powers the join-with-existing-grants warning."""

import uuid

import pytest

from src.models.realm import Realm
from src.models.service_app import ServiceApp


async def _noop():
    pass


def _app(realm_id=None) -> ServiceApp:
    return ServiceApp(
        id=uuid.uuid4(), name="Docs", service_name="docs",
        key_hash="x" * 64, key_prefix="sk_xxxx****",
        allowed_origins=[], allowed_idp_audiences=[], realm_id=realm_id,
    )


class _Scalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _Result:
    def __init__(self, items=None, first=None):
        self._items = items or []
        self._first = first

    def scalars(self):
        return _Scalars(self._items)

    def first(self):
        return self._first


class _FakeDB:
    """get() -> preset obj; execute() -> queued results (FIFO); records delete()."""

    def __init__(self, get_result=None, results=None):
        self._get = get_result
        self._results = list(results or [])
        self.deleted = []

    async def get(self, _model, _pk):
        return self._get

    async def execute(self, _stmt):
        return self._results.pop(0)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        pass


def test_realm_slug_pattern_is_letter_start():
    import re

    from src.schemas.validators import REALM_SLUG_PATTERN

    assert re.match(REALM_SLUG_PATTERN, "acme-suite")
    assert not re.match(REALM_SLUG_PATTERN, "9acme")  # must start with a letter
    assert not re.match(REALM_SLUG_PATTERN, "-acme")
    assert not re.match(REALM_SLUG_PATTERN, "acme-")


@pytest.mark.asyncio
async def test_update_realm_sets_provided_fields():
    from src.services import realm_service

    realm = Realm(id=uuid.uuid4(), name="Old", slug="acme-suite", m2m_ttl_s=300)
    out = await realm_service.update_realm(
        _FakeDB(get_result=realm), realm.id, name="New", m2m_ttl_s=120, is_active=False
    )
    assert out.name == "New"
    assert out.m2m_ttl_s == 120
    assert out.is_active is False


@pytest.mark.asyncio
async def test_update_realm_missing_returns_none():
    from src.services import realm_service

    assert await realm_service.update_realm(_FakeDB(get_result=None), uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_delete_realm_deletes_and_invalidates_cache(monkeypatch):
    from src.services import realm_service, service_app_service

    monkeypatch.setattr(service_app_service, "_invalidate_cache", _noop)
    realm = Realm(id=uuid.uuid4(), name="A", slug="acme-suite", m2m_ttl_s=300)
    db = _FakeDB(get_result=realm)
    assert await realm_service.delete_realm(db, realm.id) is True
    assert realm in db.deleted


@pytest.mark.asyncio
async def test_delete_realm_missing_returns_false():
    from src.services import realm_service

    assert await realm_service.delete_realm(_FakeDB(get_result=None), uuid.uuid4()) is False


@pytest.mark.asyncio
async def test_list_members_returns_apps():
    from src.services import realm_service

    apps = [_app(), _app()]
    out = await realm_service.list_members(_FakeDB(results=[_Result(items=apps)]), uuid.uuid4())
    assert out == apps


@pytest.mark.asyncio
async def test_service_app_has_grants_true_when_action_exists():
    from src.services import realm_service

    # First execute() (ServiceAction probe) returns a row -> short-circuits True.
    db = _FakeDB(results=[_Result(first=("id",))])
    assert await realm_service.service_app_has_grants(db, "docs") is True


@pytest.mark.asyncio
async def test_service_app_has_grants_false_when_none():
    from src.services import realm_service

    # ServiceAction probe empty, then ResourcePermission probe empty -> False.
    db = _FakeDB(results=[_Result(first=None), _Result(first=None)])
    assert await realm_service.service_app_has_grants(db, "docs") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_realm_admin_service.py -v`
Expected: FAIL — `ImportError` for `REALM_SLUG_PATTERN` and `AttributeError` for the new `realm_service` functions.

- [ ] **Step 3: Add `REALM_SLUG_PATTERN`**

In `service/src/schemas/validators.py`, immediately after the `SLUG_PATTERN = ...` line, add:

```python
# Realm slugs are letter-start (they stand in for ``service_name``, which is
# letter-start), unlike the org/workspace SLUG_PATTERN above which allows a digit start.
REALM_SLUG_PATTERN = r"^[a-z][a-z0-9-]*[a-z0-9]$"
```

- [ ] **Step 4: Add the admin operations to `realm_service`**

In `service/src/services/realm_service.py`, append:

```python
async def update_realm(
    db: AsyncSession,
    realm_id: uuid.UUID,
    *,
    name: str | None = None,
    m2m_ttl_s: int | None = None,
    is_active: bool | None = None,
) -> Realm | None:
    """Patch a realm's mutable fields. Slug is intentionally NOT updatable (it keys
    effective_scope). Returns None if the realm doesn't exist."""
    realm = await db.get(Realm, realm_id)
    if realm is None:
        return None
    if name is not None:
        realm.name = name
    if m2m_ttl_s is not None:
        realm.m2m_ttl_s = m2m_ttl_s
    if is_active is not None:
        realm.is_active = is_active
    await db.flush()
    return realm


async def delete_realm(db: AsyncSession, realm_id: uuid.UUID) -> bool:
    """Delete a realm. The service_apps.realm_id FK is ON DELETE SET NULL, so members
    revert to standalone — invalidate the service-key cache (it stores realm slugs)."""
    from src.services import service_app_service

    realm = await db.get(Realm, realm_id)
    if realm is None:
        return False
    await db.delete(realm)
    await db.flush()
    await service_app_service._invalidate_cache()
    return True


async def list_members(db: AsyncSession, realm_id: uuid.UUID) -> list[ServiceApp]:
    result = await db.execute(
        select(ServiceApp)
        .where(ServiceApp.realm_id == realm_id)
        .order_by(ServiceApp.name)
    )
    return list(result.scalars().all())


async def service_app_has_grants(db: AsyncSession, service_name: str) -> bool:
    """True if the service already has RBAC actions or resource permissions under its
    own ``service_name``. Joining a realm won't surface those under the new shared
    scope (v1 has no auto-migrate), so the admin UI warns before adding."""
    from src.models.permission import ResourcePermission
    from src.models.role import ServiceAction

    actions = await db.execute(
        select(ServiceAction.id).where(ServiceAction.service_name == service_name).limit(1)
    )
    if actions.first() is not None:
        return True
    perms = await db.execute(
        select(ResourcePermission.id)
        .where(ResourcePermission.service_name == service_name)
        .limit(1)
    )
    return perms.first() is not None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd service && uv run pytest tests/test_realm_admin_service.py -v`
Expected: PASS (8 passed).

- [ ] **Step 6: Lint + commit**

```bash
cd service && uv run ruff format $FILES && uv run ruff check --fix $FILES   # $FILES = ONLY this task's files; never 'ruff format .' / 'make fmt'
git add service/src/schemas/validators.py service/src/services/realm_service.py \
  service/tests/test_realm_admin_service.py
git commit -m "feat(realm): admin realm_service ops (update/delete/members/has_grants) + slug pattern"
```

---

### Task 2: Realm admin request/response schemas

**Files:**
- Modify: `service/src/schemas/realm.py` (add admin CRUD + member schemas)
- Test: `service/tests/test_realm_admin_schemas.py`

**Interfaces:**
- Consumes: `REALM_SLUG_PATTERN` (Task 1); `SafeStr`, `NonEmptySafeStrOptional` (`schemas/validators.py`).
- Produces:
  - `RealmCreateRequest(name: SafeStr, slug: str[REALM_SLUG_PATTERN], m2m_ttl_s: int = 300)`
  - `RealmUpdateRequest(name: NonEmptySafeStrOptional = None, m2m_ttl_s: int | None = None, is_active: bool | None = None)`
  - `RealmResponse(id, slug, name, m2m_ttl_s, is_active, created_at)` (from_attributes)
  - `RealmMemberResponse(id, name, service_name, has_grants: bool)`

- [ ] **Step 1: Write the failing test**

```python
# service/tests/test_realm_admin_schemas.py
"""Realm admin schemas: letter-start slug validation, ttl bounds, member shape."""

import uuid

import pytest
from pydantic import ValidationError


def test_create_accepts_valid_slug_and_defaults_ttl():
    from src.schemas.realm import RealmCreateRequest

    body = RealmCreateRequest(name="Acme Suite", slug="acme-suite")
    assert body.slug == "acme-suite"
    assert body.m2m_ttl_s == 300


def test_create_rejects_digit_start_slug():
    from src.schemas.realm import RealmCreateRequest

    with pytest.raises(ValidationError):
        RealmCreateRequest(name="X", slug="9acme")


def test_create_rejects_out_of_range_ttl():
    from src.schemas.realm import RealmCreateRequest

    with pytest.raises(ValidationError):
        RealmCreateRequest(name="X", slug="acme-suite", m2m_ttl_s=5)  # below floor


def test_update_all_fields_optional():
    from src.schemas.realm import RealmUpdateRequest

    body = RealmUpdateRequest()
    assert body.name is None and body.m2m_ttl_s is None and body.is_active is None


def test_update_has_no_slug_field():
    from src.schemas.realm import RealmUpdateRequest

    assert "slug" not in RealmUpdateRequest.model_fields  # slug is immutable


def test_member_response_shape():
    from src.schemas.realm import RealmMemberResponse

    m = RealmMemberResponse(id=uuid.uuid4(), name="Docs", service_name="docs", has_grants=True)
    assert m.service_name == "docs"
    assert m.has_grants is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_realm_admin_schemas.py -v`
Expected: FAIL — `ImportError` for `RealmCreateRequest` etc.

- [ ] **Step 3: Add the schemas**

In `service/src/schemas/realm.py`, add the imports at the top (the file currently imports only `from pydantic import BaseModel, Field`):

```python
import uuid
from datetime import datetime

from src.schemas.validators import REALM_SLUG_PATTERN, NonEmptySafeStrOptional, SafeStr
```

and append:

```python
class RealmCreateRequest(BaseModel):
    name: SafeStr = Field(min_length=1, max_length=255)
    slug: str = Field(pattern=REALM_SLUG_PATTERN, max_length=255)
    # No-user m2m token lifetime (seconds): 30s floor, 1h ceiling.
    m2m_ttl_s: int = Field(default=300, ge=30, le=3600)


class RealmUpdateRequest(BaseModel):
    # Slug is intentionally absent — it keys effective_scope and is immutable.
    name: NonEmptySafeStrOptional = None
    m2m_ttl_s: int | None = Field(default=None, ge=30, le=3600)
    is_active: bool | None = None


class RealmResponse(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    m2m_ttl_s: int
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class RealmMemberResponse(BaseModel):
    id: uuid.UUID
    name: str
    service_name: str
    # True if this member already had grants under its own service_name when it
    # joined — those are NOT visible under the realm scope (v1 has no auto-migrate).
    has_grants: bool
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd service && uv run pytest tests/test_realm_admin_schemas.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Lint + commit**

```bash
cd service && uv run ruff format $FILES && uv run ruff check --fix $FILES   # $FILES = ONLY this task's files; never 'ruff format .' / 'make fmt'
git add service/src/schemas/realm.py service/tests/test_realm_admin_schemas.py
git commit -m "feat(realm): admin realm CRUD + member schemas (letter-start slug, immutable)"
```

---

### Task 3: `/admin/realms` CRUD endpoints

**Files:**
- Modify: `service/src/api/admin_routes.py` (add a `# ── Realms ──` section + `realm_service` import)
- Test: `service/tests/test_realm_admin_crud.py`

**Interfaces:**
- Consumes: `realm_service.create_realm/get_realm/list_realms/update_realm/delete_realm` (Plan 1 + Task 1); `RealmCreateRequest`, `RealmUpdateRequest`, `RealmResponse` (Task 2); `require_admin`, `activity_service.log_activity` (existing).
- Produces: `GET/POST /admin/realms`, `GET/PATCH/DELETE /admin/realms/{realm_id}`. Mutations audit-logged.

- [ ] **Step 1: Write the failing test**

```python
# service/tests/test_realm_admin_crud.py
"""Behavioral tests for /admin/realms CRUD — require_admin + get_db overridden,
realm_service + activity log mocked (house behavioral style)."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import admin_routes
from src.api.admin_routes import router as admin_router
from src.api.dependencies import require_admin
from src.database import get_db
from src.models.realm import Realm


class _FakeDB:
    async def commit(self):
        pass


def _build_app(monkeypatch):
    app = FastAPI()
    app.include_router(admin_router)
    app.dependency_overrides[require_admin] = lambda: {"sub": str(uuid.uuid4())}

    async def _db():
        yield _FakeDB()

    app.dependency_overrides[get_db] = _db

    async def _log(*a, **k):
        return None

    monkeypatch.setattr(admin_routes.activity_service, "log_activity", _log)
    return app


def _realm(slug="acme-suite", name="Acme Suite"):
    return Realm(id=uuid.uuid4(), slug=slug, name=name, m2m_ttl_s=300, is_active=True)


def test_create_realm_returns_201_and_audits(monkeypatch):
    app = _build_app(monkeypatch)
    created = {}

    async def _create(_db, *, name, slug, m2m_ttl_s=300, created_by=None):
        created["name"], created["slug"] = name, slug
        return _realm(slug=slug, name=name)

    logged = {}

    async def _log(_db, **k):
        logged.update(k)

    monkeypatch.setattr(admin_routes.realm_service, "create_realm", _create)
    monkeypatch.setattr(admin_routes.activity_service, "log_activity", _log)

    resp = TestClient(app).post(
        "/admin/realms",
        json={"name": "Acme Suite", "slug": "acme-suite"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 201
    assert resp.json()["slug"] == "acme-suite"
    assert created == {"name": "Acme Suite", "slug": "acme-suite"}
    assert logged["action"] == "realm_created"


def test_list_realms(monkeypatch):
    app = _build_app(monkeypatch)

    async def _list(_db):
        return [_realm()]

    monkeypatch.setattr(admin_routes.realm_service, "list_realms", _list)
    resp = TestClient(app).get("/admin/realms")
    assert resp.status_code == 200
    assert resp.json()[0]["slug"] == "acme-suite"


def test_get_realm_detail_404_when_missing(monkeypatch):
    app = _build_app(monkeypatch)

    async def _get(_db, _id):
        return None

    monkeypatch.setattr(admin_routes.realm_service, "get_realm", _get)
    resp = TestClient(app).get(f"/admin/realms/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_update_realm_audits(monkeypatch):
    app = _build_app(monkeypatch)

    async def _update(_db, _id, *, name=None, m2m_ttl_s=None, is_active=None):
        return _realm(name=name or "Acme Suite")

    logged = {}

    async def _log(_db, **k):
        logged.update(k)

    monkeypatch.setattr(admin_routes.realm_service, "update_realm", _update)
    monkeypatch.setattr(admin_routes.activity_service, "log_activity", _log)
    resp = TestClient(app).patch(
        f"/admin/realms/{uuid.uuid4()}",
        json={"name": "Renamed"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200
    assert logged["action"] == "realm_updated"


def test_delete_realm_204_and_audits(monkeypatch):
    app = _build_app(monkeypatch)

    async def _delete(_db, _id):
        return True

    logged = {}

    async def _log(_db, **k):
        logged.update(k)

    monkeypatch.setattr(admin_routes.realm_service, "delete_realm", _delete)
    monkeypatch.setattr(admin_routes.activity_service, "log_activity", _log)
    resp = TestClient(app).delete(
        f"/admin/realms/{uuid.uuid4()}", headers={"X-Requested-With": "XMLHttpRequest"}
    )
    assert resp.status_code == 204
    assert logged["action"] == "realm_deleted"


def test_delete_realm_404_when_missing(monkeypatch):
    app = _build_app(monkeypatch)

    async def _delete(_db, _id):
        return False

    monkeypatch.setattr(admin_routes.realm_service, "delete_realm", _delete)
    resp = TestClient(app).delete(
        f"/admin/realms/{uuid.uuid4()}", headers={"X-Requested-With": "XMLHttpRequest"}
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_realm_admin_crud.py -v`
Expected: FAIL — the `/admin/realms` routes don't exist (404), and `admin_routes.realm_service` has no attribute to patch (AttributeError) until the import is added.

- [ ] **Step 3: Import `realm_service` + the realm schemas in `admin_routes.py`**

In `service/src/api/admin_routes.py`, add `realm_service` to the existing `from src.services import (...)` import block (it already imports `activity_service`, `admin_service`, `workspace_service`, `service_app_service`, etc. — add `realm_service` to that tuple). Then add the schema import near the other schema imports:

```python
from src.schemas.realm import (
    RealmCreateRequest,
    RealmMemberResponse,
    RealmResponse,
    RealmUpdateRequest,
)
```

- [ ] **Step 4: Add the Realms CRUD section**

In `service/src/api/admin_routes.py`, append at the end of the file:

```python
# ── Realms ────────────────────────────────────────────────────────────


@router.get("/realms", response_model=list[RealmResponse])
async def list_realms(db: AsyncSession = Depends(get_db)):
    return await realm_service.list_realms(db)


@router.post("/realms", response_model=RealmResponse, status_code=201)
async def create_realm(
    body: RealmCreateRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    actor_id = uuid.UUID(admin["sub"])
    realm = await realm_service.create_realm(
        db, name=body.name, slug=body.slug, m2m_ttl_s=body.m2m_ttl_s, created_by=actor_id
    )
    await activity_service.log_activity(
        db,
        action="realm_created",
        target_type="realm",
        target_id=realm.id,
        actor_id=actor_id,
        detail={"name": realm.name, "slug": realm.slug},
    )
    await db.commit()
    return realm


@router.get("/realms/{realm_id}", response_model=RealmResponse)
async def get_realm_detail(realm_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    realm = await realm_service.get_realm(db, realm_id)
    if realm is None:
        raise HTTPException(status_code=404, detail="Realm not found")
    return realm


@router.patch("/realms/{realm_id}", response_model=RealmResponse)
async def update_realm(
    realm_id: uuid.UUID,
    body: RealmUpdateRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    realm = await realm_service.update_realm(
        db, realm_id, name=body.name, m2m_ttl_s=body.m2m_ttl_s, is_active=body.is_active
    )
    if realm is None:
        raise HTTPException(status_code=404, detail="Realm not found")
    await activity_service.log_activity(
        db,
        action="realm_updated",
        target_type="realm",
        target_id=realm_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"name": realm.name},
    )
    await db.commit()
    return realm


@router.delete("/realms/{realm_id}", status_code=204)
async def delete_realm(
    realm_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    deleted = await realm_service.delete_realm(db, realm_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Realm not found")
    await activity_service.log_activity(
        db,
        action="realm_deleted",
        target_type="realm",
        target_id=realm_id,
        actor_id=uuid.UUID(admin["sub"]),
    )
    await db.commit()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd service && uv run pytest tests/test_realm_admin_crud.py -v`
Expected: PASS (6 passed).

- [ ] **Step 6: Run the full suite (catch import fallout in the big admin module)**

Run: `cd service && uv run pytest tests/ -q`
Expected: PASS for the realm/admin tests. IdP/JWKS **connection** failures are the known sandbox artifact.

- [ ] **Step 7: Lint + commit**

```bash
cd service && uv run ruff format $FILES && uv run ruff check --fix $FILES   # $FILES = ONLY this task's files; never 'ruff format .' / 'make fmt'
git add service/src/api/admin_routes.py service/tests/test_realm_admin_crud.py
git commit -m "feat(realm): /admin/realms CRUD endpoints + audit events"
```

---

### Task 4: `/admin/realms/{id}/members` — list / add / remove

**Files:**
- Modify: `service/src/api/admin_routes.py` (extend the `# ── Realms ──` section)
- Test: `service/tests/test_realm_admin_membership.py`

**Interfaces:**
- Consumes: `realm_service.get_realm/add_member/remove_member/list_members/service_app_has_grants` (Plan 1 + Task 1); `service_app_service.get_service_app` (existing); `RealmMemberResponse` (Task 2); `require_admin`, `activity_service.log_activity`.
- Produces: `GET /admin/realms/{realm_id}/members`, `POST /admin/realms/{realm_id}/members/{service_app_id}` (409 on cross-realm), `DELETE /admin/realms/{realm_id}/members/{service_app_id}`.

- [ ] **Step 1: Write the failing test**

```python
# service/tests/test_realm_admin_membership.py
"""Behavioral tests for /admin/realms/{id}/members — list (with has_grants), add
(one-realm-max 409 guard), remove. require_admin + get_db overridden; services mocked."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import admin_routes
from src.api.admin_routes import router as admin_router
from src.api.dependencies import require_admin
from src.database import get_db
from src.models.realm import Realm
from src.models.service_app import ServiceApp


class _FakeDB:
    async def commit(self):
        pass


def _build_app(monkeypatch):
    app = FastAPI()
    app.include_router(admin_router)
    app.dependency_overrides[require_admin] = lambda: {"sub": str(uuid.uuid4())}

    async def _db():
        yield _FakeDB()

    app.dependency_overrides[get_db] = _db

    async def _log(*a, **k):
        return None

    monkeypatch.setattr(admin_routes.activity_service, "log_activity", _log)
    return app


def _app(realm_id=None) -> ServiceApp:
    return ServiceApp(
        id=uuid.uuid4(), name="Docs", service_name="docs",
        key_hash="x" * 64, key_prefix="sk_xxxx****",
        allowed_origins=[], allowed_idp_audiences=[], realm_id=realm_id,
    )


def _realm():
    return Realm(id=uuid.uuid4(), slug="acme-suite", name="Acme Suite", m2m_ttl_s=300, is_active=True)


def test_list_members_includes_has_grants(monkeypatch):
    app = _build_app(monkeypatch)
    members = [_app()]

    async def _members(_db, _rid):
        return members

    async def _has(_db, _svc):
        return True

    monkeypatch.setattr(admin_routes.realm_service, "list_members", _members)
    monkeypatch.setattr(admin_routes.realm_service, "service_app_has_grants", _has)
    resp = TestClient(app).get(f"/admin/realms/{uuid.uuid4()}/members")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["service_name"] == "docs"
    assert body[0]["has_grants"] is True


def test_add_member_standalone_app_succeeds_and_audits(monkeypatch):
    app = _build_app(monkeypatch)
    realm = _realm()
    candidate = _app(realm_id=None)

    async def _get_realm(_db, _rid):
        return realm

    async def _get_app(_db, _aid):
        return candidate

    async def _add(_db, _rid, _aid):
        return candidate

    async def _has(_db, _svc):
        return False

    logged = {}

    async def _log(_db, **k):
        logged.update(k)

    monkeypatch.setattr(admin_routes.realm_service, "get_realm", _get_realm)
    monkeypatch.setattr(admin_routes.service_app_service, "get_service_app", _get_app)
    monkeypatch.setattr(admin_routes.realm_service, "add_member", _add)
    monkeypatch.setattr(admin_routes.realm_service, "service_app_has_grants", _has)
    monkeypatch.setattr(admin_routes.activity_service, "log_activity", _log)

    resp = TestClient(app).post(
        f"/admin/realms/{realm.id}/members/{candidate.id}",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 201
    assert resp.json()["has_grants"] is False
    assert logged["action"] == "realm_member_added"


def test_add_member_already_in_other_realm_409(monkeypatch):
    app = _build_app(monkeypatch)
    realm = _realm()
    other = _app(realm_id=uuid.uuid4())  # already in a DIFFERENT realm

    async def _get_realm(_db, _rid):
        return realm

    async def _get_app(_db, _aid):
        return other

    monkeypatch.setattr(admin_routes.realm_service, "get_realm", _get_realm)
    monkeypatch.setattr(admin_routes.service_app_service, "get_service_app", _get_app)

    resp = TestClient(app).post(
        f"/admin/realms/{realm.id}/members/{other.id}",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 409


def test_add_member_realm_missing_404(monkeypatch):
    app = _build_app(monkeypatch)

    async def _get_realm(_db, _rid):
        return None

    monkeypatch.setattr(admin_routes.realm_service, "get_realm", _get_realm)
    resp = TestClient(app).post(
        f"/admin/realms/{uuid.uuid4()}/members/{uuid.uuid4()}",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 404


def test_remove_member_204_and_audits(monkeypatch):
    app = _build_app(monkeypatch)
    member = _app(realm_id=uuid.uuid4())

    async def _get_app(_db, _aid):
        return member

    async def _remove(_db, _aid):
        return member

    logged = {}

    async def _log(_db, **k):
        logged.update(k)

    monkeypatch.setattr(admin_routes.service_app_service, "get_service_app", _get_app)
    monkeypatch.setattr(admin_routes.realm_service, "remove_member", _remove)
    monkeypatch.setattr(admin_routes.activity_service, "log_activity", _log)
    resp = TestClient(app).delete(
        f"/admin/realms/{uuid.uuid4()}/members/{member.id}",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 204
    assert logged["action"] == "realm_member_removed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_realm_admin_membership.py -v`
Expected: FAIL — the membership routes don't exist (404/405).

- [ ] **Step 3: Add the membership endpoints**

In `service/src/api/admin_routes.py`, append to the `# ── Realms ──` section (after `delete_realm`):

```python
async def _member_response(db, app) -> RealmMemberResponse:
    return RealmMemberResponse(
        id=app.id,
        name=app.name,
        service_name=app.service_name,
        has_grants=await realm_service.service_app_has_grants(db, app.service_name),
    )


@router.get(
    "/realms/{realm_id}/members", response_model=list[RealmMemberResponse]
)
async def list_realm_members(realm_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    members = await realm_service.list_members(db, realm_id)
    return [await _member_response(db, app) for app in members]


@router.post(
    "/realms/{realm_id}/members/{service_app_id}",
    response_model=RealmMemberResponse,
    status_code=201,
)
async def add_realm_member(
    realm_id: uuid.UUID,
    service_app_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    realm = await realm_service.get_realm(db, realm_id)
    if realm is None:
        raise HTTPException(status_code=404, detail="Realm not found")
    app = await service_app_service.get_service_app(db, service_app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Service app not found")
    # One-realm-max: refuse to silently steal an app from another realm.
    if app.realm_id is not None and app.realm_id != realm_id:
        raise HTTPException(
            status_code=409,
            detail="Service app already belongs to another realm; remove it first",
        )
    await realm_service.add_member(db, realm_id, service_app_id)
    await activity_service.log_activity(
        db,
        action="realm_member_added",
        target_type="realm",
        target_id=realm_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"service_app_id": str(service_app_id), "service_name": app.service_name},
    )
    await db.commit()
    return await _member_response(db, app)


@router.delete(
    "/realms/{realm_id}/members/{service_app_id}", status_code=204
)
async def remove_realm_member(
    realm_id: uuid.UUID,
    service_app_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    app = await service_app_service.get_service_app(db, service_app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Service app not found")
    await realm_service.remove_member(db, service_app_id)
    await activity_service.log_activity(
        db,
        action="realm_member_removed",
        target_type="realm",
        target_id=realm_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"service_app_id": str(service_app_id), "service_name": app.service_name},
    )
    await db.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd service && uv run pytest tests/test_realm_admin_membership.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Lint + commit**

```bash
cd service && uv run ruff format $FILES && uv run ruff check --fix $FILES   # $FILES = ONLY this task's files; never 'ruff format .' / 'make fmt'
git add service/src/api/admin_routes.py service/tests/test_realm_admin_membership.py
git commit -m "feat(realm): /admin/realms membership add/remove/list (+ one-realm-max guard)"
```

---

### Task 5: Surface `realm_id` on `ServiceAppResponse`

**Files:**
- Modify: `service/src/schemas/service_app.py` (add `realm_id` to `ServiceAppResponse`)
- Test: `service/tests/test_service_app_realm_field.py`

**Interfaces:**
- Consumes: `ServiceApp.realm_id` column (Plan 1).
- Produces: `ServiceAppResponse.realm_id: uuid.UUID | None`. The admin `/admin/service-apps` + `/admin/service-apps/{id}` endpoints already build `ServiceAppResponse` via `from_attributes`, so the column auto-populates — the UI maps `realm_id` → realm name from its realms list (no `realm_name` needed on the API).

- [ ] **Step 1: Write the failing test**

```python
# service/tests/test_service_app_realm_field.py
"""ServiceAppResponse exposes realm_id so the admin UI can show realm membership."""

import uuid
from datetime import UTC, datetime


def test_service_app_response_carries_realm_id():
    from src.schemas.service_app import ServiceAppResponse

    rid = uuid.uuid4()
    resp = ServiceAppResponse(
        id=uuid.uuid4(),
        name="Docs",
        service_name="docs",
        key_prefix="sk_xxxx****",
        is_active=True,
        allowed_origins=[],
        allowed_idp_audiences=[],
        last_used_at=None,
        created_by=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        realm_id=rid,
    )
    assert resp.realm_id == rid


def test_service_app_response_realm_id_defaults_none():
    from src.schemas.service_app import ServiceAppResponse

    resp = ServiceAppResponse(
        id=uuid.uuid4(),
        name="Docs",
        service_name="docs",
        key_prefix="sk_xxxx****",
        is_active=True,
        allowed_origins=[],
        allowed_idp_audiences=[],
        last_used_at=None,
        created_by=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert resp.realm_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_service_app_realm_field.py -v`
Expected: FAIL — `ServiceAppResponse` has no `realm_id` (the first test errors on the unexpected kwarg under Pydantic's default, or the assertion fails).

- [ ] **Step 3: Add `realm_id` to `ServiceAppResponse`**

In `service/src/schemas/service_app.py`, add to `ServiceAppResponse` (after `service_name`):

```python
    realm_id: uuid.UUID | None = None
```

(`uuid` is already imported in this file.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd service && uv run pytest tests/test_service_app_realm_field.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the full suite (no regression to service-app endpoints)**

Run: `cd service && uv run pytest tests/ -q`
Expected: PASS — existing service-app tests still pass (the new field defaults to None). IdP/JWKS connection failures are the sandbox artifact.

- [ ] **Step 6: Lint + commit**

```bash
cd service && uv run ruff format $FILES && uv run ruff check --fix $FILES   # $FILES = ONLY this task's files; never 'ruff format .' / 'make fmt'
git add service/src/schemas/service_app.py service/tests/test_service_app_realm_field.py
git commit -m "feat(realm): expose realm_id on ServiceAppResponse (admin UI membership display)"
```

---

## Self-review (done by plan author)

**Spec coverage (Plan 4 backend slice):**
- `/admin/realms` CRUD (name, slug, m2m_ttl_s) → Task 3. Membership add/remove + list → Task 4.
- Audit events `realm_created/updated/deleted/member_added/member_removed` → Tasks 3-4 (via `activity_service.log_activity`).
- Realm-`slug` schema validation `^[a-z][a-z0-9-]*[a-z0-9]$` (deferred from Plan 1) → Task 1 (`REALM_SLUG_PATTERN`) + Task 2 (enforced in `RealmCreateRequest`).
- One-realm-max on add → Task 4 (409 guard) + the single FK.
- Join-with-existing-grants warning support → Task 1 (`service_app_has_grants`) + Task 4 (`has_grants` on member responses).
- Service App detail shows realm → Task 5 (`realm_id` on `ServiceAppResponse`; UI resolves the name).
- Admin cookie + `X-Requested-With` CSRF → inherited from `admin_router`'s `require_admin` dependency (no per-endpoint work).
- **NO `main.py` change** — `admin_router` is already registered on the public tier (Plan 3).
- **Deferred to the follow-on React UI plan:** Realms list/detail/members pages, ServiceAppDetail realm display, routes + sidebar nav, the type-to-confirm delete dialog (frontend convention), the grants warning UI.

**Placeholder scan:** none — every code/command step carries full content.

**Type consistency:** `update_realm`/`delete_realm`/`list_members`/`service_app_has_grants` signatures match across Task 1 (def), Task 3-4 (callers + monkeypatch). `RealmCreateRequest`/`RealmUpdateRequest`/`RealmResponse`/`RealmMemberResponse` defined in Task 2, used in Tasks 3-4. `REALM_SLUG_PATTERN` defined Task 1, consumed Task 2. `realm_id` field named identically in Task 5 schema + test.

**Known integration gaps (call out at execution):** the behavioral admin tests mock `realm_service` + the activity log + use a fake `db` (only `.commit()` is exercised), so the real DB writes + the FK ON DELETE SET NULL behavior are runtime-verified (`make start`), not unit-verified — consistent with the rest of the suite's pure-unit style. The full end-to-end (admin UI → these endpoints) lands with the follow-on React plan.
