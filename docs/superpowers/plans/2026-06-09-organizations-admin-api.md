# Organizations Admin API (Plan 2a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the admin CRUD API for organizations, their email domains, the public-sign-in toggle, and per-workspace allowed-orgs — the backend the Phase-2 React UI (Plan 2b) will consume.

**Architecture:** A new `org_admin_service.py` holds the admin CRUD/guard logic (kept separate from the hot-path `organization_service.py`), raising typed exceptions (`OrgNotFound`/`OrgConflict`/`OrgProtected`) that a new `org_admin_routes.py` router (its own `APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])`) maps to 404/409/400. Schemas extend `schemas/admin.py`. Mutations are activity-logged then committed, mirroring the existing service-apps admin endpoints.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, PostgreSQL, Pydantic v2, pytest (fake-session unit tests + `TestClient`+`dependency_overrides` route tests — the repo has no DB fixtures).

**Spec:** `docs/superpowers/specs/2026-06-09-organizations-admin-design.md`. This is **Plan 2a of 2** (Plan 2b = React UI, written after this lands).

---

## Reference patterns (read these once)

- **Admin route CRUD:** `service/src/api/admin_routes.py` service-apps section (~line 1275): `@router.post(..., status_code=201)` + `@limiter.limit("5/minute")`, `actor_id = uuid.UUID(admin["sub"])`, `activity_service.log_activity(...)` then `await db.commit()`, `ValueError`→`HTTPException(404/400)`.
- **Service layer:** `service/src/services/service_app_service.py` — functions take `db`, raise `ValueError`, `db.add`/`db.flush`.
- **Schemas:** `service/src/schemas/admin.py` — `BaseModel`, `SafeStr`/`SafeStrOptional`, `Field(pattern=...)`, `model_config = {"from_attributes": True}`.
- **Router registration:** `service/src/main.py:9` (import) + `:187` (`include_router`).
- **`require_admin`:** `service/src/api/dependencies.py` — returns the admin JWT payload dict (`admin["sub"]`, `admin["admin"]`); does CSRF + admin re-check. Overridable in tests.
- **Route test style:** `service/tests/test_authz_resolve_guard.py` — build a `FastAPI()`, `include_router`, set `limiter.enabled = False`, override deps via `app.dependency_overrides`, drive with `TestClient`.
- **Existing org models:** `service/src/models/organization.py` (`Organization`, `OrganizationDomain`, `WorkspaceAllowedOrganization`); resolution helpers in `service/src/services/organization_service.py` (`normalize_domain`).

## File Structure

**Create:**
- `service/src/services/org_admin_service.py` — admin CRUD/guards for orgs, domains, allowed-orgs.
- `service/src/api/org_admin_routes.py` — the 10 admin endpoints.
- `service/tests/test_org_admin_service.py` — fake-session unit tests.
- `service/tests/test_org_admin_routes.py` — route tests (guards + wiring).

**Modify:**
- `service/src/schemas/admin.py` — add org request/response models.
- `service/src/main.py` — import + register the new router.

---

### Task 1: `org_admin_service` — exceptions + org CRUD

**Files:**
- Create: `service/src/services/org_admin_service.py`
- Test: `service/tests/test_org_admin_service.py`

- [ ] **Step 1: Write the failing tests** (create `service/tests/test_org_admin_service.py`)

```python
"""org_admin_service CRUD + guard logic, via fake sessions (no DB)."""

import uuid

import pytest

from src.services import org_admin_service as svc


class _Result:
    def __init__(self, *, scalar=None, scalars=None):
        self._scalar = scalar
        self._scalars = scalars or []

    def scalar_one_or_none(self):
        return self._scalar

    def scalar_one(self):
        return self._scalar

    def scalars(self):
        parent = self

        class _S:
            def all(self_inner):
                return parent._scalars

        return _S()


class _FakeDB:
    """Serves queued execute() results; records added/deleted objects."""

    def __init__(self, *, execute_results=None, get_results=None):
        self._exec = list(execute_results or [])
        self._get = list(get_results or [])
        self.added = []
        self.deleted = []
        self.flushed = False

    async def execute(self, _stmt):
        return self._exec.pop(0)

    async def get(self, _model, _pk):
        return self._get.pop(0) if self._get else None

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        self.flushed = True


class _Org:
    def __init__(self, is_public=False):
        self.id = uuid.uuid4()
        self.name = "TAMU"
        self.slug = "tamu"
        self.is_public = is_public
        self.enabled = True


@pytest.mark.asyncio
async def test_create_organization_ok():
    db = _FakeDB(execute_results=[_Result(scalar=None)])  # slug not taken
    org = await svc.create_organization(db, name="TAMU", slug="tamu")
    assert org in db.added
    assert org.slug == "tamu"
    assert org.is_public is False
    assert db.flushed


@pytest.mark.asyncio
async def test_create_organization_duplicate_slug_conflicts():
    db = _FakeDB(execute_results=[_Result(scalar=uuid.uuid4())])  # slug taken
    with pytest.raises(svc.OrgConflict):
        await svc.create_organization(db, name="TAMU", slug="tamu")


@pytest.mark.asyncio
async def test_update_organization_not_found():
    db = _FakeDB(get_results=[None])
    with pytest.raises(svc.OrgNotFound):
        await svc.update_organization(db, uuid.uuid4(), name="New")


@pytest.mark.asyncio
async def test_update_organization_sets_fields():
    org = _Org()
    db = _FakeDB(get_results=[org])
    await svc.update_organization(db, org.id, name="Texas A&M", enabled=False)
    assert org.name == "Texas A&M"
    assert org.enabled is False


@pytest.mark.asyncio
async def test_delete_public_org_is_protected():
    pub = _Org(is_public=True)
    db = _FakeDB(get_results=[pub])
    with pytest.raises(svc.OrgProtected):
        await svc.delete_organization(db, pub.id)
    assert pub not in db.deleted


@pytest.mark.asyncio
async def test_delete_regular_org_ok():
    org = _Org()
    db = _FakeDB(get_results=[org])
    await svc.delete_organization(db, org.id)
    assert org in db.deleted
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest tests/test_org_admin_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.services.org_admin_service'`.

- [ ] **Step 3: Create the service with exceptions + org CRUD** (`service/src/services/org_admin_service.py`)

```python
"""Admin CRUD + guards for organizations, domains, and workspace allowed-orgs.

Kept separate from organization_service.py (the hot-path resolution/enforcement
module used on every sign-in). Raises typed exceptions the routes map to HTTP:
OrgNotFound -> 404, OrgConflict -> 409, OrgProtected -> 400.
"""

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.organization import (
    Organization,
    OrganizationDomain,
    WorkspaceAllowedOrganization,
)
from src.models.user import User
from src.models.workspace import Workspace
from src.services import organization_service


class OrgNotFound(Exception):
    """Organization / domain / workspace does not exist."""


class OrgConflict(Exception):
    """Slug or domain already in use."""


class OrgProtected(Exception):
    """Operation not allowed on the public organization."""


async def create_organization(
    db: AsyncSession, name: str, slug: str
) -> Organization:
    taken = (
        await db.execute(select(Organization.id).where(Organization.slug == slug))
    ).scalar_one_or_none()
    if taken is not None:
        raise OrgConflict(f"Organization slug {slug!r} is already in use")
    org = Organization(name=name, slug=slug, is_public=False, enabled=True)
    db.add(org)
    await db.flush()
    return org


async def update_organization(
    db: AsyncSession,
    org_id: uuid.UUID,
    name: str | None = None,
    enabled: bool | None = None,
) -> Organization:
    org = await db.get(Organization, org_id)
    if org is None:
        raise OrgNotFound("Organization not found")
    if name is not None:
        org.name = name
    if enabled is not None:
        org.enabled = enabled
    await db.flush()
    return org


async def delete_organization(db: AsyncSession, org_id: uuid.UUID) -> None:
    org = await db.get(Organization, org_id)
    if org is None:
        raise OrgNotFound("Organization not found")
    if org.is_public:
        raise OrgProtected("The public organization cannot be deleted")
    await db.delete(org)
    await db.flush()


async def list_organizations(db: AsyncSession) -> list[dict]:
    """List orgs with domain + user counts; public org sorted first."""
    dc = (
        select(
            OrganizationDomain.organization_id.label("oid"),
            func.count().label("c"),
        )
        .group_by(OrganizationDomain.organization_id)
        .subquery()
    )
    uc = (
        select(User.organization_id.label("oid"), func.count().label("c"))
        .group_by(User.organization_id)
        .subquery()
    )
    stmt = (
        select(
            Organization,
            func.coalesce(dc.c.c, 0),
            func.coalesce(uc.c.c, 0),
        )
        .outerjoin(dc, dc.c.oid == Organization.id)
        .outerjoin(uc, uc.c.oid == Organization.id)
        .order_by(Organization.is_public.desc(), Organization.name)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {"org": org, "domain_count": d, "user_count": u} for org, d, u in rows
    ]


async def get_organization_detail(
    db: AsyncSession, org_id: uuid.UUID
) -> dict | None:
    org = await db.get(Organization, org_id)
    if org is None:
        return None
    domains = (
        (
            await db.execute(
                select(OrganizationDomain)
                .where(OrganizationDomain.organization_id == org_id)
                .order_by(OrganizationDomain.domain)
            )
        )
        .scalars()
        .all()
    )
    user_count = (
        await db.execute(
            select(func.count()).where(User.organization_id == org_id)
        )
    ).scalar_one()
    return {"org": org, "domains": list(domains), "user_count": user_count}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest tests/test_org_admin_service.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/sidx/workspace/identity-service
git add service/src/services/org_admin_service.py service/tests/test_org_admin_service.py
git commit -m "feat(org-admin): org CRUD service + typed guard exceptions

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: domains in `org_admin_service`

**Files:**
- Modify: `service/src/services/org_admin_service.py` (append)
- Test: `service/tests/test_org_admin_service.py` (append)

- [ ] **Step 1: Write the failing tests** (append to `service/tests/test_org_admin_service.py`)

```python
@pytest.mark.asyncio
async def test_add_domain_normalizes_and_inserts():
    org = _Org()
    db = _FakeDB(get_results=[org], execute_results=[_Result(scalar=None)])
    d = await svc.add_domain(db, org.id, "  Mail.TAMU.edu ", include_subdomains=True)
    assert d.domain == "mail.tamu.edu"  # normalized
    assert d.include_subdomains is True
    assert d in db.added


@pytest.mark.asyncio
async def test_add_domain_to_public_org_is_protected():
    pub = _Org(is_public=True)
    db = _FakeDB(get_results=[pub])
    with pytest.raises(svc.OrgProtected):
        await svc.add_domain(db, pub.id, "tamu.edu", include_subdomains=False)


@pytest.mark.asyncio
async def test_add_invalid_domain_rejected():
    org = _Org()
    db = _FakeDB(get_results=[org])
    with pytest.raises(ValueError):
        await svc.add_domain(db, org.id, "not-a-domain", include_subdomains=False)


@pytest.mark.asyncio
async def test_add_duplicate_domain_conflicts():
    org = _Org()
    db = _FakeDB(get_results=[org], execute_results=[_Result(scalar=uuid.uuid4())])
    with pytest.raises(svc.OrgConflict):
        await svc.add_domain(db, org.id, "tamu.edu", include_subdomains=False)


@pytest.mark.asyncio
async def test_remove_domain_wrong_org_not_found():
    class _Dom:
        id = uuid.uuid4()
        organization_id = uuid.uuid4()  # belongs to a different org

    db = _FakeDB(get_results=[_Dom()])
    with pytest.raises(svc.OrgNotFound):
        await svc.remove_domain(db, uuid.uuid4(), uuid.uuid4())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest tests/test_org_admin_service.py -k domain -v`
Expected: FAIL with `AttributeError: module 'src.services.org_admin_service' has no attribute 'add_domain'`.

- [ ] **Step 3: Append the domain functions** (`service/src/services/org_admin_service.py`)

```python
async def add_domain(
    db: AsyncSession,
    org_id: uuid.UUID,
    domain: str,
    include_subdomains: bool,
) -> OrganizationDomain:
    org = await db.get(Organization, org_id)
    if org is None:
        raise OrgNotFound("Organization not found")
    if org.is_public:
        raise OrgProtected(
            "The public organization is the catch-all and cannot have domains"
        )
    normalized = organization_service.normalize_domain(domain)
    if normalized is None:
        raise ValueError(f"Invalid domain: {domain!r}")
    taken = (
        await db.execute(
            select(OrganizationDomain.id).where(
                OrganizationDomain.domain == normalized
            )
        )
    ).scalar_one_or_none()
    if taken is not None:
        raise OrgConflict(
            f"Domain {normalized!r} is already claimed by an organization"
        )
    row = OrganizationDomain(
        organization_id=org_id,
        domain=normalized,
        include_subdomains=include_subdomains,
    )
    db.add(row)
    await db.flush()
    return row


async def remove_domain(
    db: AsyncSession, org_id: uuid.UUID, domain_id: uuid.UUID
) -> None:
    row = await db.get(OrganizationDomain, domain_id)
    if row is None or row.organization_id != org_id:
        raise OrgNotFound("Domain not found")
    await db.delete(row)
    await db.flush()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest tests/test_org_admin_service.py -v`
Expected: PASS (11 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/sidx/workspace/identity-service
git add service/src/services/org_admin_service.py service/tests/test_org_admin_service.py
git commit -m "feat(org-admin): add/remove domain with normalize + uniqueness guards

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: allowed-orgs + users in `org_admin_service`

**Files:**
- Modify: `service/src/services/org_admin_service.py` (append)
- Test: `service/tests/test_org_admin_service.py` (append)

- [ ] **Step 1: Write the failing tests** (append to `service/tests/test_org_admin_service.py`)

```python
@pytest.mark.asyncio
async def test_set_allowed_orgs_replaces_and_validates():
    ws_id = uuid.uuid4()
    a, b = uuid.uuid4(), uuid.uuid4()
    # get(Workspace) -> exists; execute #1 validates ids (both found);
    # execute #2 is the delete of existing rows.
    db = _FakeDB(
        get_results=[object()],
        execute_results=[_Result(scalars=[a, b]), _Result()],
    )
    await svc.set_workspace_allowed_orgs(db, ws_id, [a, b, a])  # dup ignored
    added = [
        o
        for o in db.added
        if isinstance(o, WorkspaceAllowedOrganization)
    ]
    assert {o.organization_id for o in added} == {a, b}
    assert all(o.workspace_id == ws_id for o in added)


@pytest.mark.asyncio
async def test_set_allowed_orgs_unknown_id_rejected():
    ws_id = uuid.uuid4()
    a, missing = uuid.uuid4(), uuid.uuid4()
    db = _FakeDB(get_results=[object()], execute_results=[_Result(scalars=[a])])
    with pytest.raises(ValueError):
        await svc.set_workspace_allowed_orgs(db, ws_id, [a, missing])


@pytest.mark.asyncio
async def test_set_allowed_orgs_workspace_not_found():
    db = _FakeDB(get_results=[None])
    with pytest.raises(svc.OrgNotFound):
        await svc.set_workspace_allowed_orgs(db, uuid.uuid4(), [])


@pytest.mark.asyncio
async def test_set_allowed_orgs_empty_clears():
    ws_id = uuid.uuid4()
    db = _FakeDB(get_results=[object()], execute_results=[_Result()])  # delete only
    await svc.set_workspace_allowed_orgs(db, ws_id, [])
    assert not [
        o for o in db.added if isinstance(o, WorkspaceAllowedOrganization)
    ]
```

Note: `WorkspaceAllowedOrganization` is already imported at the top of the test file? It is not — add `from src.models.organization import WorkspaceAllowedOrganization` to the imports at the top of `service/tests/test_org_admin_service.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest tests/test_org_admin_service.py -k allowed -v`
Expected: FAIL with `AttributeError: ... has no attribute 'set_workspace_allowed_orgs'`.

- [ ] **Step 3: Append allowed-orgs + users functions** (`service/src/services/org_admin_service.py`)

```python
async def get_workspace_allowed_orgs(
    db: AsyncSession, workspace_id: uuid.UUID
) -> list[Organization]:
    stmt = (
        select(Organization)
        .join(
            WorkspaceAllowedOrganization,
            WorkspaceAllowedOrganization.organization_id == Organization.id,
        )
        .where(WorkspaceAllowedOrganization.workspace_id == workspace_id)
        .order_by(Organization.name)
    )
    return list((await db.execute(stmt)).scalars().all())


async def set_workspace_allowed_orgs(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    organization_ids: list[uuid.UUID],
) -> None:
    """Replace the workspace's allowed-org set. Empty list = open to all."""
    workspace = await db.get(Workspace, workspace_id)
    if workspace is None:
        raise OrgNotFound("Workspace not found")
    ids = list(dict.fromkeys(organization_ids))  # dedupe, keep order
    if ids:
        found = set(
            (
                await db.execute(
                    select(Organization.id).where(Organization.id.in_(ids))
                )
            )
            .scalars()
            .all()
        )
        missing = [i for i in ids if i not in found]
        if missing:
            raise ValueError(
                f"Unknown organization ids: {[str(m) for m in missing]}"
            )
    await db.execute(
        delete(WorkspaceAllowedOrganization).where(
            WorkspaceAllowedOrganization.workspace_id == workspace_id
        )
    )
    for oid in ids:
        db.add(
            WorkspaceAllowedOrganization(
                workspace_id=workspace_id, organization_id=oid
            )
        )
    await db.flush()


async def list_org_users(
    db: AsyncSession, org_id: uuid.UUID, limit: int = 50, offset: int = 0
) -> tuple[list[User], int]:
    total = (
        await db.execute(
            select(func.count()).where(User.organization_id == org_id)
        )
    ).scalar_one()
    users = (
        (
            await db.execute(
                select(User)
                .where(User.organization_id == org_id)
                .order_by(User.email)
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return list(users), total
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest tests/test_org_admin_service.py -v`
Expected: PASS (15 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/sidx/workspace/identity-service
git add service/src/services/org_admin_service.py service/tests/test_org_admin_service.py
git commit -m "feat(org-admin): workspace allowed-orgs set/get + list org users

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: schemas

**Files:**
- Modify: `service/src/schemas/admin.py` (append)
- Test: `service/tests/test_org_admin_schemas.py`

- [ ] **Step 1: Write the failing test** (create `service/tests/test_org_admin_schemas.py`)

```python
"""Validation rules on the org admin request schemas."""

import uuid

import pytest
from pydantic import ValidationError

from src.schemas.admin import (
    AdminOrgCreateRequest,
    AdminOrgDomainCreateRequest,
    AdminWorkspaceAllowedOrgsRequest,
)


def test_org_create_accepts_valid_slug():
    req = AdminOrgCreateRequest(name="TAMU", slug="tamu")
    assert req.slug == "tamu"


@pytest.mark.parametrize("bad", ["Tamu", "-tamu", "tamu-", "ta mu", "a", "t@mu"])
def test_org_create_rejects_bad_slug(bad):
    with pytest.raises(ValidationError):
        AdminOrgCreateRequest(name="X", slug=bad)


def test_domain_request_defaults_include_subdomains_false():
    req = AdminOrgDomainCreateRequest(domain="tamu.edu")
    assert req.include_subdomains is False


def test_allowed_orgs_request_takes_uuid_list():
    ids = [uuid.uuid4(), uuid.uuid4()]
    req = AdminWorkspaceAllowedOrgsRequest(organization_ids=ids)
    assert req.organization_ids == ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest tests/test_org_admin_schemas.py -v`
Expected: FAIL with `ImportError: cannot import name 'AdminOrgCreateRequest'`.

- [ ] **Step 3: Append the schemas** (`service/src/schemas/admin.py`)

Append at the end of the file (the file already imports `uuid`, `datetime`, `BaseModel`, `Field`, `SafeStr`, `SafeStrOptional`):

```python
class AdminOrgResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    is_public: bool
    enabled: bool
    domain_count: int
    user_count: int


class AdminOrgDomainResponse(BaseModel):
    id: uuid.UUID
    domain: str
    include_subdomains: bool

    model_config = {"from_attributes": True}


class AdminOrgDetailResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    is_public: bool
    enabled: bool
    user_count: int
    domains: list[AdminOrgDomainResponse]


class AdminOrgCreateRequest(BaseModel):
    name: SafeStr = Field(min_length=1, max_length=255)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$", max_length=63)


class AdminOrgUpdateRequest(BaseModel):
    name: SafeStrOptional = None
    enabled: bool | None = None


class AdminOrgDomainCreateRequest(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    include_subdomains: bool = False


class AdminWorkspaceAllowedOrgsRequest(BaseModel):
    organization_ids: list[uuid.UUID]


class AdminWorkspaceAllowedOrgsResponse(BaseModel):
    organization_ids: list[uuid.UUID]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest tests/test_org_admin_schemas.py -v`
Expected: PASS (8 passed, counting the parametrized cases).

- [ ] **Step 5: Commit**

```bash
cd /Users/sidx/workspace/identity-service
git add service/src/schemas/admin.py service/tests/test_org_admin_schemas.py
git commit -m "feat(org-admin): request/response schemas

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: routes + registration

**Files:**
- Create: `service/src/api/org_admin_routes.py`
- Modify: `service/src/main.py`
- Test: `service/tests/test_org_admin_routes.py`

- [ ] **Step 1: Write the failing tests** (create `service/tests/test_org_admin_routes.py`)

```python
"""Org admin routes: guard + wiring tests with overridden deps (no real DB).

Mirrors tests/test_authz_resolve_guard.py: a minimal app, limiter disabled,
require_admin + get_db overridden, driven by TestClient.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded

from src.api.dependencies import require_admin
from src.api.org_admin_routes import router as org_router
from src.database import get_db
from src.middleware.rate_limit import limiter, rate_limit_exceeded_handler
from src.services import org_admin_service

limiter.enabled = False

_XRW = {"X-Requested-With": "XMLHttpRequest"}


class _Org:
    def __init__(self, is_public=False):
        self.id = uuid.uuid4()
        self.name = "Public" if is_public else "TAMU"
        self.slug = "public" if is_public else "tamu"
        self.is_public = is_public
        self.enabled = True


class _FakeDB:
    def __init__(self, get_results=None):
        self._get = list(get_results or [])

    async def get(self, _model, _pk):
        return self._get.pop(0) if self._get else None

    def add(self, _obj):
        pass

    async def delete(self, _obj):
        pass

    async def flush(self):
        pass

    async def commit(self):
        pass

    async def refresh(self, _obj):
        pass

    async def execute(self, _stmt):
        raise AssertionError("unexpected execute in this test")


def _app(db) -> FastAPI:
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(org_router)
    app.dependency_overrides[require_admin] = lambda: {
        "sub": str(uuid.uuid4()),
        "admin": True,
    }

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    return app


def test_delete_public_org_returns_400(monkeypatch):
    pub = _Org(is_public=True)
    client = TestClient(_app(_FakeDB(get_results=[pub])))
    resp = client.delete(f"/admin/organizations/{pub.id}", headers=_XRW)
    assert resp.status_code == 400
    assert "public" in resp.json()["detail"].lower()


def test_delete_missing_org_returns_404():
    client = TestClient(_app(_FakeDB(get_results=[None])))
    resp = client.delete(f"/admin/organizations/{uuid.uuid4()}", headers=_XRW)
    assert resp.status_code == 404


def test_create_org_duplicate_slug_returns_409(monkeypatch):
    async def _boom(db, name, slug):
        raise org_admin_service.OrgConflict("slug taken")

    monkeypatch.setattr(org_admin_service, "create_organization", _boom)
    client = TestClient(_app(_FakeDB()))
    resp = client.post(
        "/admin/organizations",
        json={"name": "TAMU", "slug": "tamu"},
        headers=_XRW,
    )
    assert resp.status_code == 409


def test_create_org_ok_returns_201(monkeypatch):
    org = _Org()

    async def _ok(db, name, slug):
        return org

    monkeypatch.setattr(org_admin_service, "create_organization", _ok)
    client = TestClient(_app(_FakeDB()))
    resp = client.post(
        "/admin/organizations",
        json={"name": "TAMU", "slug": "tamu"},
        headers=_XRW,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["slug"] == "tamu"
    assert body["domain_count"] == 0


def test_set_allowed_orgs_unknown_id_returns_400(monkeypatch):
    async def _boom(db, ws, ids):
        raise ValueError("Unknown organization ids: ['x']")

    monkeypatch.setattr(org_admin_service, "set_workspace_allowed_orgs", _boom)
    client = TestClient(_app(_FakeDB()))
    resp = client.put(
        f"/admin/workspaces/{uuid.uuid4()}/allowed-organizations",
        json={"organization_ids": [str(uuid.uuid4())]},
        headers=_XRW,
    )
    assert resp.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest tests/test_org_admin_routes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.api.org_admin_routes'`.

- [ ] **Step 3: Create the routes module** (`service/src/api/org_admin_routes.py`)

```python
"""Admin endpoints for organizations, domains, and workspace allowed-orgs."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import require_admin
from src.database import get_db
from src.middleware.rate_limit import limiter
from src.schemas.admin import (
    AdminOrgCreateRequest,
    AdminOrgDetailResponse,
    AdminOrgDomainCreateRequest,
    AdminOrgDomainResponse,
    AdminOrgResponse,
    AdminOrgUpdateRequest,
    AdminUserResponse,
    AdminWorkspaceAllowedOrgsRequest,
    AdminWorkspaceAllowedOrgsResponse,
)
from src.services import activity_service, org_admin_service
from src.services.org_admin_service import OrgConflict, OrgNotFound, OrgProtected

router = APIRouter(
    prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)]
)


def _org_response(org, domain_count: int, user_count: int) -> AdminOrgResponse:
    return AdminOrgResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        is_public=org.is_public,
        enabled=org.enabled,
        domain_count=domain_count,
        user_count=user_count,
    )


@router.get("/organizations", response_model=list[AdminOrgResponse])
async def list_organizations(db: AsyncSession = Depends(get_db)):
    rows = await org_admin_service.list_organizations(db)
    return [
        _org_response(r["org"], r["domain_count"], r["user_count"]) for r in rows
    ]


@router.post("/organizations", response_model=AdminOrgResponse, status_code=201)
@limiter.limit("5/minute")
async def create_organization(
    request: Request,
    body: AdminOrgCreateRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        org = await org_admin_service.create_organization(
            db, name=body.name, slug=body.slug
        )
    except OrgConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    await activity_service.log_activity(
        db,
        action="org_create",
        target_type="organization",
        target_id=org.id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"name": org.name, "slug": org.slug},
    )
    await db.commit()
    await db.refresh(org)
    return _org_response(org, 0, 0)


@router.get("/organizations/{org_id}", response_model=AdminOrgDetailResponse)
async def get_organization(
    org_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    detail = await org_admin_service.get_organization_detail(db, org_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    org = detail["org"]
    return AdminOrgDetailResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        is_public=org.is_public,
        enabled=org.enabled,
        user_count=detail["user_count"],
        domains=[
            AdminOrgDomainResponse.model_validate(d) for d in detail["domains"]
        ],
    )


@router.patch("/organizations/{org_id}", response_model=AdminOrgDetailResponse)
async def update_organization(
    org_id: uuid.UUID,
    body: AdminOrgUpdateRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        org = await org_admin_service.update_organization(
            db, org_id, name=body.name, enabled=body.enabled
        )
    except OrgNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    action = (
        "org_public_toggle" if org.is_public else "org_update"
    )
    await activity_service.log_activity(
        db,
        action=action,
        target_type="organization",
        target_id=org.id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"name": org.name, "enabled": org.enabled},
    )
    await db.commit()
    detail = await org_admin_service.get_organization_detail(db, org_id)
    org = detail["org"]
    return AdminOrgDetailResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        is_public=org.is_public,
        enabled=org.enabled,
        user_count=detail["user_count"],
        domains=[
            AdminOrgDomainResponse.model_validate(d) for d in detail["domains"]
        ],
    )


@router.delete("/organizations/{org_id}", status_code=204)
async def delete_organization(
    org_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        await org_admin_service.delete_organization(db, org_id)
    except OrgNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except OrgProtected as e:
        raise HTTPException(status_code=400, detail=str(e))
    await activity_service.log_activity(
        db,
        action="org_delete",
        target_type="organization",
        target_id=org_id,
        actor_id=uuid.UUID(admin["sub"]),
    )
    await db.commit()


@router.post(
    "/organizations/{org_id}/domains",
    response_model=AdminOrgDomainResponse,
    status_code=201,
)
@limiter.limit("10/minute")
async def add_domain(
    request: Request,
    org_id: uuid.UUID,
    body: AdminOrgDomainCreateRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        row = await org_admin_service.add_domain(
            db,
            org_id,
            body.domain,
            include_subdomains=body.include_subdomains,
        )
    except OrgNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except OrgProtected as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OrgConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await activity_service.log_activity(
        db,
        action="org_domain_add",
        target_type="organization",
        target_id=org_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"domain": row.domain, "include_subdomains": row.include_subdomains},
    )
    await db.commit()
    await db.refresh(row)
    return AdminOrgDomainResponse.model_validate(row)


@router.delete(
    "/organizations/{org_id}/domains/{domain_id}", status_code=204
)
async def remove_domain(
    org_id: uuid.UUID,
    domain_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        await org_admin_service.remove_domain(db, org_id, domain_id)
    except OrgNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    await activity_service.log_activity(
        db,
        action="org_domain_remove",
        target_type="organization",
        target_id=org_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"domain_id": str(domain_id)},
    )
    await db.commit()


@router.get(
    "/organizations/{org_id}/users", response_model=list[AdminUserResponse]
)
async def list_org_users(
    org_id: uuid.UUID,
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    users, _total = await org_admin_service.list_org_users(
        db, org_id, limit=limit, offset=offset
    )
    return [
        AdminUserResponse(
            id=u.id,
            email=u.email,
            name=u.name,
            avatar_url=u.avatar_url,
            is_active=u.is_active,
            is_admin=u.is_admin,
            created_at=u.created_at,
            workspace_count=0,
        )
        for u in users
    ]


@router.get(
    "/workspaces/{workspace_id}/allowed-organizations",
    response_model=AdminWorkspaceAllowedOrgsResponse,
)
async def get_allowed_orgs(
    workspace_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    orgs = await org_admin_service.get_workspace_allowed_orgs(db, workspace_id)
    return AdminWorkspaceAllowedOrgsResponse(
        organization_ids=[o.id for o in orgs]
    )


@router.put(
    "/workspaces/{workspace_id}/allowed-organizations",
    response_model=AdminWorkspaceAllowedOrgsResponse,
)
async def set_allowed_orgs(
    workspace_id: uuid.UUID,
    body: AdminWorkspaceAllowedOrgsRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        await org_admin_service.set_workspace_allowed_orgs(
            db, workspace_id, body.organization_ids
        )
    except OrgNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await activity_service.log_activity(
        db,
        action="workspace_allowed_orgs_set",
        target_type="workspace",
        target_id=workspace_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"organization_ids": [str(i) for i in body.organization_ids]},
    )
    await db.commit()
    return AdminWorkspaceAllowedOrgsResponse(
        organization_ids=body.organization_ids
    )
```

- [ ] **Step 4: Register the router** (`service/src/main.py`)

Add the import next to the other route-router imports (near line 9):

```python
from src.api.org_admin_routes import router as org_admin_router
```

And register it next to the others (near line 187, after `app.include_router(admin_router)`):

```python
app.include_router(org_admin_router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest tests/test_org_admin_routes.py -v`
Expected: PASS (5 passed).

- [ ] **Step 6: Commit**

```bash
cd /Users/sidx/workspace/identity-service
git add service/src/api/org_admin_routes.py service/src/main.py service/tests/test_org_admin_routes.py
git commit -m "feat(org-admin): admin routes for orgs/domains/allowed-orgs

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: live-DB smoke + full suite + lint

The aggregate-count queries (`list_organizations`, `get_organization_detail`,
`list_org_users`) and the allowed-orgs replace are SQL the fake-session tests
can't meaningfully exercise. Verify them against the running dev Postgres
(`localhost:9001`, already migrated with the org tables + seeded public org).

**Files:** none committed (verification only).

- [ ] **Step 1: Run the org-admin service round-trip against the real DB**

Run (one-off; creates then deletes its own rows):

```bash
cd /Users/sidx/workspace/identity-service/service && uv run python -c "
import asyncio, uuid
from src.database import async_session_factory
from src.services import org_admin_service as svc

async def main():
    async with async_session_factory() as db:
        org = await svc.create_organization(db, name='Smoke Org', slug='smoke-org')
        await svc.add_domain(db, org.id, 'Smoke.Example', include_subdomains=True)
        await db.commit()
        rows = await svc.list_organizations(db)
        mine = [r for r in rows if r['org'].id == org.id][0]
        assert mine['domain_count'] == 1, mine
        assert rows[0]['org'].is_public is True, 'public org must sort first'
        detail = await svc.get_organization_detail(db, org.id)
        assert detail['domains'][0].domain == 'smoke.example'
        # cleanup
        await svc.delete_organization(db, org.id)
        await db.commit()
        print('SMOKE OK: counts + ordering + normalization verified')

asyncio.run(main())
"
```
Expected: prints `SMOKE OK: ...` with no assertion error. (`async_session_factory` is the session maker exported by `src/database.py`.)

- [ ] **Step 2: Full suite + lint**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest -q`
Expected: all PASS (existing + the new org-admin tests).

Run: `cd /Users/sidx/workspace/identity-service && make lint`
Expected: clean. If formatting is flagged, run `make fmt` and re-check.

- [ ] **Step 3: Commit (only if `make fmt` changed anything)**

```bash
cd /Users/sidx/workspace/identity-service
git add -A service/
git commit -m "style(org-admin): formatting

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- §2 endpoints (10) → Task 5 wires all ten. ✓
- §3 service module `org_admin_service.py` → Tasks 1–3. ✓
- §4 schemas → Task 4. ✓
- §5 guards: slug 409 (T1), domain normalize + global-unique 409 (T2), public-org no-delete / no-domains / 400 (T1/T2 + route mapping T5), allowed-orgs validate ids (T3) → covered. ✓
- §7 audit logging: `org_create`/`org_update`/`org_public_toggle`/`org_delete`/`org_domain_add`/`org_domain_remove`/`workspace_allowed_orgs_set` → all in Task 5 routes. ✓
- §Testing 2a: service unit tests (T1–T3), route guard/wiring tests (T5), aggregate-SQL smoke (T6). ✓
- **Not in this plan (Plan 2b):** React UI, System-Settings mirror.

**Placeholder scan:** none — every step has complete code/commands. The one conditional is Task 6's note to confirm the session-factory name in `src/database.py` (a real environmental check, not a placeholder).

**Type consistency:** Service raises `OrgNotFound`/`OrgConflict`/`OrgProtected` (Task 1) + `ValueError` for invalid input; routes (Task 5) map them 404/409/400/400 consistently. `list_organizations` returns `[{"org","domain_count","user_count"}]` (T1) consumed by `_org_response` (T5). `get_organization_detail` returns `{"org","domains","user_count"}` (T1) consumed by the detail routes (T5). `set_workspace_allowed_orgs(db, workspace_id, organization_ids)` signature matches the route call (T3↔T5). Schema names (`AdminOrgResponse`, `AdminOrgDetailResponse`, `AdminOrgDomainResponse`, `AdminOrgCreateRequest`, `AdminOrgUpdateRequest`, `AdminOrgDomainCreateRequest`, `AdminWorkspaceAllowedOrgsRequest/Response`) are identical across Tasks 4 and 5.
