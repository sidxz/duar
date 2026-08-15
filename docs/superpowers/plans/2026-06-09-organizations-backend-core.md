# Organizations (Email-Domain Tenancy) — Backend Core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate every IdP sign-in by the user's email-domain organization, carry the org in user + service-to-service tokens, and let workspaces restrict which orgs may be members — with a zero-lockout migration.

**Architecture:** Org membership is *resolved* (not invited): one email domain → one org, recomputed on every sign-in. A pure domain-matcher (exact + opt-in subdomains) plus a thin DB resolver decide the org; a singleton public org (toggleable `enabled`) is the catch-all. The org becomes a `users.organization_id` FK and three new claims (`oid`/`oslug`/`opub`) on the access and authz tokens. Workspaces gain an allowed-orgs join table enforced at token issuance and member-invite.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, PostgreSQL 16, Alembic, PyJWT (RS256), pytest (unit tests with hand-rolled fake sessions — the repo has no DB fixtures/conftest).

**Scope:** This is **Plan 1 of 3** (backend engine). Out of scope here, in later plans: admin CRUD API for orgs/domains/public-toggle/allowed-orgs + React admin UI (Plan 2); SDK org-claim exposure in `sdk/` + `sdks/` (Plan 3). After this plan, real orgs are created by DB seed; the migration seeds the public org so default sign-in keeps working.

---

## Reference: design decisions (from the spec)

Spec: `docs/superpowers/specs/2026-06-09-organizations-email-domain-tenancy-design.md`.

- **WS↔Org:** membership *filter*, manual invite. Empty allowed-orgs set = open to all (today's behavior). Org does not own workspaces.
- **Default posture:** public ON; migration seeds the public org and backfills all existing users into it.
- **Domain match:** exact + per-row `include_subdomains`; most-specific (longest) subdomain wins.
- **Token claims:** `oid` / `oslug` / `opub` on access + authz tokens.
- **Issuance enforcement:** token issuance is the authoritative gate (tightening an allowlist can strand existing members — intended).
- **Disable-public:** turning the public org off blocks all public-org users at next sign-in.

## File Structure

**Create:**
- `service/src/models/organization.py` — `Organization`, `OrganizationDomain`, `WorkspaceAllowedOrganization` models.
- `service/src/services/organization_service.py` — `normalize_domain`, `match_org_id` (pure), `resolve_organization`, `workspace_allows_org`.
- `service/migrations/versions/<rev>_add_organizations.py` — tables + `users.organization_id` + seed + backfill.
- `service/tests/test_org_domain_matching.py` — pure matcher/normalizer tests.
- `service/tests/test_org_resolution.py` — `resolve_organization` + `workspace_allows_org` tests.
- `service/tests/test_org_token_claims.py` — access + authz token org-claim tests.

**Modify:**
- `service/src/models/__init__.py` — register the 3 new models so Alembic metadata sees them.
- `service/src/models/user.py:12-28` — add `organization_id` FK column.
- `service/src/services/auth_service.py` — `find_or_create_user` gains `organization_id`; `issue_tokens` + `rotate_refresh_token` load org + enforce allowlist + pass claims.
- `service/src/api/auth_routes.py:212-221` — resolve org, gate sign-in (403), pass `organization_id`.
- `service/src/auth/jwt.py:30-55,90-122` — `create_access_token` + `create_authz_token` gain org params/claims.
- `service/src/api/authz_routes.py` — load org + pass claims into `create_authz_token` (Task 7); **and** (added during final review — `/authz/resolve` is a third sign-in/token-mint path) resolve + gate the org at JIT provisioning, pass `organization_id` into `find_or_create_user`, and enforce `workspace_allows_org` before minting. Covered by `tests/test_authz_org_gate.py`.
- `service/src/services/workspace_service.py:108-129` — `invite_member` enforces allowed-orgs.
- `service/tests/test_preprovisioned_link.py` — pass `organization_id` to the updated `find_or_create_user`.

**Conventions to match:**
- Models: `Mapped[...] = mapped_column(...)`, `postgresql.UUID(as_uuid=True)`, string-ref FKs, `__table_args__` for constraints/indexes (see `service/src/models/workspace.py`).
- Tests: one self-contained fake session per file; no real DB (see `service/tests/test_preprovisioned_link.py`, `test_authz_jwt.py`).
- Run tests from the `service/` dir.
- Every commit message ends with the `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` trailer.

---

### Task 1: Organization models

**Files:**
- Create: `service/src/models/organization.py`
- Modify: `service/src/models/__init__.py`
- Test: `service/tests/test_org_domain_matching.py` (Task 1 portion)

- [ ] **Step 1: Write the failing test** (create `service/tests/test_org_domain_matching.py`)

```python
"""Organization model shape + pure domain-matching logic."""

import uuid


def test_organization_models_have_expected_shape():
    from src.models.organization import (
        Organization,
        OrganizationDomain,
        WorkspaceAllowedOrganization,
    )

    assert Organization.__tablename__ == "organizations"
    for col in ("id", "slug", "name", "is_public", "enabled", "created_by"):
        assert col in Organization.__table__.columns

    assert OrganizationDomain.__tablename__ == "organization_domains"
    for col in ("id", "organization_id", "domain", "include_subdomains"):
        assert col in OrganizationDomain.__table__.columns
    # Domain must be globally unique (a domain cannot belong to two orgs).
    assert any(
        c.name == "uq_org_domain"
        for c in OrganizationDomain.__table__.constraints
    )

    assert WorkspaceAllowedOrganization.__tablename__ == "workspace_allowed_organizations"
    for col in ("id", "workspace_id", "organization_id"):
        assert col in WorkspaceAllowedOrganization.__table__.columns


def test_models_registered_for_metadata():
    import src.models as m

    assert "Organization" in m.__all__
    assert "OrganizationDomain" in m.__all__
    assert "WorkspaceAllowedOrganization" in m.__all__
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest tests/test_org_domain_matching.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.models.organization'`.

- [ ] **Step 3: Create the model module** (`service/src/models/organization.py`)

```python
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.database import Base


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (
        # At most one public org — enforced by a partial unique index.
        Index(
            "uq_one_public_org",
            "is_public",
            unique=True,
            postgresql_where=text("is_public"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # For the public org, `enabled` is the public-sign-in switch. For a real org,
    # `enabled=False` is a kill-switch that blocks all of its users at next login.
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    domains: Mapped[list["OrganizationDomain"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class OrganizationDomain(Base):
    __tablename__ = "organization_domains"
    __table_args__ = (
        UniqueConstraint("domain", name="uq_org_domain"),
        Index("ix_organization_domains_org_id", "organization_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE")
    )
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    include_subdomains: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    organization: Mapped["Organization"] = relationship(back_populates="domains")


class WorkspaceAllowedOrganization(Base):
    __tablename__ = "workspace_allowed_organizations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "organization_id", name="uq_workspace_allowed_org"
        ),
        Index("ix_workspace_allowed_orgs_workspace_id", "workspace_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

- [ ] **Step 4: Register the models** (`service/src/models/__init__.py`)

Add this import after the existing `from src.models.role import ...` line:

```python
from src.models.organization import (
    Organization,
    OrganizationDomain,
    WorkspaceAllowedOrganization,
)
```

And add these three entries to the end of the `__all__` list (before the closing `]`):

```python
    "Organization",
    "OrganizationDomain",
    "WorkspaceAllowedOrganization",
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest tests/test_org_domain_matching.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
cd /Users/sidx/workspace/identity-service
git add service/src/models/organization.py service/src/models/__init__.py service/tests/test_org_domain_matching.py
git commit -m "feat(models): add organization, domain, workspace-allowed-org models

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `users.organization_id` column

**Files:**
- Modify: `service/src/models/user.py:12-28`
- Test: `service/tests/test_org_domain_matching.py` (append)

- [ ] **Step 1: Write the failing test** (append to `service/tests/test_org_domain_matching.py`)

```python
def test_user_has_organization_id_column():
    from src.models.user import User

    assert "organization_id" in User.__table__.columns
    assert User.__table__.columns["organization_id"].nullable is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest tests/test_org_domain_matching.py::test_user_has_organization_id_column -v`
Expected: FAIL with `KeyError: 'organization_id'`.

- [ ] **Step 3: Add the column** (`service/src/models/user.py`)

Insert this column right after the `is_admin` column (line 22), before `created_at`:

```python
    # Resolved from the user's verified email domain on every sign-in. Nullable
    # in the schema; the sign-in gate + migration backfill guarantee it is set.
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
    )
```

(`UUID`, `ForeignKey`, `Mapped`, and `mapped_column` are already imported in this file.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest tests/test_org_domain_matching.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/sidx/workspace/identity-service
git add service/src/models/user.py service/tests/test_org_domain_matching.py
git commit -m "feat(models): add users.organization_id FK

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Pure domain normalizer + matcher

This is the security-critical core — the logic that decides which org an email belongs to. Pure functions, no DB, exhaustively tested.

**Files:**
- Create: `service/src/services/organization_service.py` (normalizer + matcher only)
- Test: `service/tests/test_org_domain_matching.py` (append)

- [ ] **Step 1: Write the failing tests** (append to `service/tests/test_org_domain_matching.py`)

```python
import pytest

from src.services import organization_service as org_svc


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Alice@TAMU.edu", "tamu.edu"),
        ("tamu.edu", "tamu.edu"),
        ("bob@mail.tamu.edu", "mail.tamu.edu"),
        ("  spaced@Example.COM  ", "example.com"),
        ("", None),
        (None, None),
        ("noatsign-nodot", None),
        ("a@b@c.com", None),          # multiple '@' is malformed -> fail closed
        ("user@", None),              # empty domain
        ("tamu.edu.", None),          # trailing dot
        ("@tamu.edu", "tamu.edu"),    # leading '@' ok, single '@'
    ],
)
def test_normalize_domain(raw, expected):
    assert org_svc.normalize_domain(raw) == expected


def test_match_exact_wins():
    a = uuid.uuid4()
    rows = [(a, "tamu.edu", False)]
    assert org_svc.match_org_id("tamu.edu", rows) == a


def test_match_subdomain_only_when_flag_set():
    a = uuid.uuid4()
    assert org_svc.match_org_id("mail.tamu.edu", [(a, "tamu.edu", True)]) == a
    assert org_svc.match_org_id("mail.tamu.edu", [(a, "tamu.edu", False)]) is None


def test_match_longest_subdomain_wins():
    a, b = uuid.uuid4(), uuid.uuid4()
    rows = [(a, "tamu.edu", True), (b, "b.tamu.edu", True)]
    assert org_svc.match_org_id("a.b.tamu.edu", rows) == b


def test_match_exact_beats_subdomain_regardless_of_order():
    a, b = uuid.uuid4(), uuid.uuid4()
    rows = [(b, "edu", True), (a, "tamu.edu", False)]
    assert org_svc.match_org_id("tamu.edu", rows) == a


def test_match_anti_spoof_not_a_real_subdomain():
    a = uuid.uuid4()
    # "eviltamu.edu" must NOT match "tamu.edu" even with subdomains on.
    assert org_svc.match_org_id("eviltamu.edu", [(a, "tamu.edu", True)]) is None


def test_match_no_rows():
    assert org_svc.match_org_id("gmail.com", []) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest tests/test_org_domain_matching.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.services.organization_service'`.

- [ ] **Step 3: Create the service with pure functions** (`service/src/services/organization_service.py`)

```python
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.organization import (
    Organization,
    OrganizationDomain,
    WorkspaceAllowedOrganization,
)


def normalize_domain(value: str | None) -> str | None:
    """Extract and normalize the domain from an email or bare domain.

    Lowercases, strips, takes the part after a single '@' (if present),
    IDNA-encodes unicode labels, and returns None for anything malformed
    (empty, multiple '@', no dot, leading/trailing dot). This keys org lookups,
    so it must fail closed.
    """
    if not value:
        return None
    candidate = value.strip().lower()
    if candidate.count("@") > 1:
        return None
    if "@" in candidate:
        candidate = candidate.split("@", 1)[1]
    if not candidate or "." not in candidate:
        return None
    if candidate.startswith(".") or candidate.endswith("."):
        return None
    try:
        candidate = candidate.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        return None
    return candidate


def match_org_id(
    domain: str,
    rows: list[tuple[uuid.UUID, str, bool]],
) -> uuid.UUID | None:
    """Pure resolver over (org_id, rule_domain, include_subdomains) rows from
    *enabled* orgs. Exact match is authoritative; otherwise the most specific
    (longest) subdomain rule whose pattern the domain is a sub-label of wins.
    """
    best_id: uuid.UUID | None = None
    best_len = -1
    for org_id, rule_domain, include_subdomains in rows:
        rule = rule_domain.lower()
        if domain == rule:
            return org_id
        if include_subdomains and domain.endswith("." + rule) and len(rule) > best_len:
            best_len = len(rule)
            best_id = org_id
    return best_id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest tests/test_org_domain_matching.py -v`
Expected: PASS (all parametrized + match cases green).

- [ ] **Step 5: Commit**

```bash
cd /Users/sidx/workspace/identity-service
git add service/src/services/organization_service.py service/tests/test_org_domain_matching.py
git commit -m "feat(org): pure email-domain normalizer + org matcher

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: DB resolver + workspace allow-check

**Files:**
- Modify: `service/src/services/organization_service.py` (append two async functions)
- Test: `service/tests/test_org_resolution.py`

- [ ] **Step 1: Write the failing tests** (create `service/tests/test_org_resolution.py`)

```python
"""resolve_organization + workspace_allows_org, exercised with fake sessions."""

import uuid

import pytest

from src.services import organization_service as org_svc


class _DomainRow(tuple):
    """Stand-in for a (org_id, domain, include_subdomains) result row."""


class _ExecResult:
    def __init__(self, *, rows=None, scalar=None, scalars=None):
        self._rows = rows or []
        self._scalar = scalar
        self._scalars = scalars or []

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        parent = self

        class _S:
            def all(self_inner):
                return parent._scalars

        return _S()


class _FakeSession:
    """Serves queued _ExecResult objects for successive execute() calls and a
    dict for get()."""

    def __init__(self, exec_results, get_map=None):
        self._exec = list(exec_results)
        self._get = get_map or {}

    async def execute(self, _stmt):
        return self._exec.pop(0)

    async def get(self, _model, pk):
        return self._get.get(pk)


@pytest.mark.asyncio
async def test_resolve_matched_org_returned():
    org_id = uuid.uuid4()
    org = object()
    session = _FakeSession(
        exec_results=[_ExecResult(rows=[(org_id, "tamu.edu", False)])],
        get_map={org_id: org},
    )
    result = await org_svc.resolve_organization(session, "alice@tamu.edu")
    assert result is org


@pytest.mark.asyncio
async def test_resolve_falls_back_to_public():
    public = object()
    session = _FakeSession(
        exec_results=[
            _ExecResult(rows=[]),          # no domain match
            _ExecResult(scalar=public),    # enabled public org
        ]
    )
    result = await org_svc.resolve_organization(session, "someone@gmail.com")
    assert result is public


@pytest.mark.asyncio
async def test_resolve_none_when_no_match_and_no_public():
    session = _FakeSession(
        exec_results=[_ExecResult(rows=[]), _ExecResult(scalar=None)]
    )
    result = await org_svc.resolve_organization(session, "someone@gmail.com")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_malformed_email_short_circuits_to_none():
    # No execute() results queued: a malformed domain must not hit the DB.
    session = _FakeSession(exec_results=[])
    result = await org_svc.resolve_organization(session, "not-an-email")
    assert result is None


@pytest.mark.asyncio
async def test_workspace_open_when_no_rows():
    session = _FakeSession(exec_results=[_ExecResult(scalars=[])])
    assert await org_svc.workspace_allows_org(session, uuid.uuid4(), uuid.uuid4()) is True


@pytest.mark.asyncio
async def test_workspace_restricts_to_allowed_set():
    allowed = uuid.uuid4()
    other = uuid.uuid4()
    session = _FakeSession(exec_results=[_ExecResult(scalars=[allowed])])
    assert await org_svc.workspace_allows_org(session, uuid.uuid4(), allowed) is True

    session = _FakeSession(exec_results=[_ExecResult(scalars=[allowed])])
    assert await org_svc.workspace_allows_org(session, uuid.uuid4(), other) is False


@pytest.mark.asyncio
async def test_workspace_denies_orgless_user_when_restricted():
    allowed = uuid.uuid4()
    session = _FakeSession(exec_results=[_ExecResult(scalars=[allowed])])
    assert await org_svc.workspace_allows_org(session, uuid.uuid4(), None) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest tests/test_org_resolution.py -v`
Expected: FAIL with `AttributeError: module 'src.services.organization_service' has no attribute 'resolve_organization'`.

- [ ] **Step 3: Append the async resolvers** (`service/src/services/organization_service.py`)

```python
async def resolve_organization(db: AsyncSession, email: str) -> Organization | None:
    """Resolve the organization a user's email belongs to.

    1. Normalize the domain (fail-closed on malformed).
    2. Match against *enabled* orgs' domains (exact, then longest subdomain).
    3. Fall back to the enabled public org.
    4. None => sign-in not permitted.
    """
    domain = normalize_domain(email)
    if domain is None:
        return None

    stmt = (
        select(
            OrganizationDomain.organization_id,
            OrganizationDomain.domain,
            OrganizationDomain.include_subdomains,
        )
        .join(Organization, Organization.id == OrganizationDomain.organization_id)
        .where(Organization.enabled.is_(True))
    )
    rows = [tuple(r) for r in (await db.execute(stmt)).all()]
    matched = match_org_id(domain, rows)
    if matched is not None:
        return await db.get(Organization, matched)

    pub_stmt = select(Organization).where(
        Organization.is_public.is_(True), Organization.enabled.is_(True)
    )
    return (await db.execute(pub_stmt)).scalar_one_or_none()


async def workspace_allows_org(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    organization_id: uuid.UUID | None,
) -> bool:
    """True if the workspace permits members from this org.

    No allowed-org rows => open to all orgs (legacy behavior). A restricted
    workspace denies users whose org is None or not in the allowed set.
    """
    stmt = select(WorkspaceAllowedOrganization.organization_id).where(
        WorkspaceAllowedOrganization.workspace_id == workspace_id
    )
    allowed = set((await db.execute(stmt)).scalars().all())
    if not allowed:
        return True
    return organization_id in allowed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest tests/test_org_resolution.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/sidx/workspace/identity-service
git add service/src/services/organization_service.py service/tests/test_org_resolution.py
git commit -m "feat(org): resolve_organization + workspace_allows_org

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Sign-in gate (callback resolves + gates, `find_or_create_user` persists org)

**Files:**
- Modify: `service/src/services/auth_service.py:37-122`
- Modify: `service/src/api/auth_routes.py:212-221`
- Modify: `service/tests/test_preprovisioned_link.py`

- [ ] **Step 1: Update the existing tests to assert org persistence** (`service/tests/test_preprovisioned_link.py`)

In `test_preprovisioned_user_is_linked_not_rejected`, add an org id and pass it; assert it lands on the linked user. Replace the body of that test with:

```python
@pytest.mark.asyncio
async def test_preprovisioned_user_is_linked_not_rejected():
    pre = _bare_user("victim@example.com")
    org_id = uuid.uuid4()
    # execute() order: SocialAccount-by-provider (miss), User-by-email (hit),
    # SocialAccount-by-user (none → bare pre-provisioned account).
    session = _FakeSession(results=[None, pre, None])

    user = await find_or_create_user(
        session,
        provider="google",
        provider_user_id="google|1",
        email="victim@example.com",
        name="Victim",
        organization_id=org_id,
    )

    assert user is pre
    assert user.organization_id == org_id
    assert any(isinstance(a, SocialAccount) for a in session.added)
    assert session.committed
```

In `test_real_cross_provider_collision_still_rejected`, add `organization_id=uuid.uuid4(),` to the `find_or_create_user(...)` call (keyword arg, anywhere in the call).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest tests/test_preprovisioned_link.py -v`
Expected: FAIL with `TypeError: find_or_create_user() got an unexpected keyword argument 'organization_id'`.

- [ ] **Step 3: Add the `organization_id` parameter and set it on every path** (`service/src/services/auth_service.py`)

Change the `find_or_create_user` signature (lines 37-45) to add `organization_id`.
It is `uuid.UUID | None`: sign-in policy (rejecting an unresolved org) is a
*route* concern, so the service simply persists whatever the caller resolved.
The user-facing callback passes a real id (it 403s on `None` first); the admin
callback passes `org.id if org else None` without gating (see note below).

```python
async def find_or_create_user(
    db: AsyncSession,
    provider: str,
    provider_user_id: str,
    email: str,
    name: str,
    organization_id: uuid.UUID | None,
    avatar_url: str | None = None,
    provider_data: dict | None = None,
) -> User:
```

Set `organization_id` on the **social-account-exists** path. After `user.name = strip_html(name)` (line 59) add:

```python
        user.organization_id = organization_id
```

Set it on the **existing bare-account link** path. After `existing.name = strip_html(name)` (line 89) add:

```python
        existing.organization_id = organization_id
```

Set it on the **new-user** path. Replace line 105:

```python
    user = User(email=email, name=strip_html(name), avatar_url=avatar_url)
```

with:

```python
    user = User(
        email=email,
        name=strip_html(name),
        avatar_url=avatar_url,
        organization_id=organization_id,
    )
```

- [ ] **Step 4: Wire resolution + the gate into the callback** (`service/src/api/auth_routes.py`)

Add the import near the other service imports at the top of the file:

```python
from src.services import organization_service
```

Then, immediately before the `try:` that calls `find_or_create_user` (currently line 212), insert the resolve-and-gate block, and add `organization_id=org.id` to the call:

```python
        org = await organization_service.resolve_organization(db, email)
        if org is None:
            return _error_page(
                403,
                "Sign-In Not Permitted",
                "Your email domain is not associated with an organization on "
                "this server, and public sign-in is disabled. Contact your "
                "administrator.",
            )

        try:
            user = await auth_service.find_or_create_user(
                db=db,
                provider=provider,
                provider_user_id=provider_user_id,
                email=email,
                name=name,
                organization_id=org.id,
                avatar_url=avatar_url,
                provider_data=profile,
            )
```

(The existing `except auth_service.CrossProviderEmailConflict:` block immediately after is unchanged.)

**Also update the second caller — `admin_callback`** (same file, the admin-panel
OAuth callback). It also calls `find_or_create_user` and would break under the new
signature. Resolve + persist the org but do NOT gate (admin access is already gated
by `is_admin`; hard org-gating here risks locking every admin out of the panel used
to configure orgs). Before its `try:` that calls `find_or_create_user`, add:

```python
        # Resolve + persist the admin's org for record-keeping, but do NOT gate
        # admin sign-in on it. Admin access is gated by is_admin (below); hard
        # org-gating here would risk locking every admin out of the panel used to
        # configure orgs (e.g. if the public org is disabled).
        org = await organization_service.resolve_organization(db, email)
```

and pass `organization_id=org.id if org else None` into that `find_or_create_user(...)` call.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest tests/test_preprovisioned_link.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
cd /Users/sidx/workspace/identity-service
git add service/src/services/auth_service.py service/src/api/auth_routes.py service/tests/test_preprovisioned_link.py
git commit -m "feat(auth): gate sign-in by org, persist users.organization_id

Resolve the org from the verified email domain in the OAuth callback; 403
when no org claims the domain and public sign-in is disabled. find_or_create_user
now records organization_id on every path (new, linked, returning).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Access-token org claims

**Files:**
- Modify: `service/src/auth/jwt.py:30-55`
- Modify: `service/src/services/auth_service.py` (`issue_tokens` ~155, `rotate_refresh_token` ~252)
- Test: `service/tests/test_org_token_claims.py` (Task 6 portion)

- [ ] **Step 1: Write the failing test** (create `service/tests/test_org_token_claims.py`)

```python
"""Org claims (oid/oslug/opub) on access + authz tokens."""

import uuid

from src.auth.jwt import (
    _AUD_ACCESS,
    create_access_token,
    decode_token,
)


def test_access_token_carries_org_claims():
    org_id = uuid.uuid4()
    token = create_access_token(
        user_id=uuid.uuid4(),
        email="alice@tamu.edu",
        name="Alice",
        workspace_id=uuid.uuid4(),
        workspace_slug="acme",
        workspace_role="editor",
        groups=[],
        org_id=str(org_id),
        org_slug="tamu",
        org_is_public=False,
    )
    payload = decode_token(token, audience=_AUD_ACCESS)
    assert payload["oid"] == str(org_id)
    assert payload["oslug"] == "tamu"
    assert payload["opub"] is False


def test_access_token_public_org_flag():
    token = create_access_token(
        user_id=uuid.uuid4(),
        email="bob@gmail.com",
        name="Bob",
        workspace_id=uuid.uuid4(),
        workspace_slug="acme",
        workspace_role="viewer",
        groups=[],
        org_id=str(uuid.uuid4()),
        org_slug="public",
        org_is_public=True,
    )
    payload = decode_token(token, audience=_AUD_ACCESS)
    assert payload["opub"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest tests/test_org_token_claims.py -v`
Expected: FAIL with `TypeError: create_access_token() got an unexpected keyword argument 'org_id'`.

- [ ] **Step 3: Add org params + claims to `create_access_token`** (`service/src/auth/jwt.py`)

Change the signature (lines 30-38) to add three params after `groups`:

```python
def create_access_token(
    user_id: uuid.UUID,
    email: str,
    name: str,
    workspace_id: uuid.UUID,
    workspace_slug: str,
    workspace_role: str,
    groups: list[uuid.UUID],
    org_id: str | None,
    org_slug: str | None,
    org_is_public: bool,
) -> str:
```

Add the claims to the payload dict, right after the `"groups": ...` line (line 50):

```python
        "oid": org_id,
        "oslug": org_slug,
        "opub": org_is_public,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest tests/test_org_token_claims.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Thread the org through both token-issuing call sites** (`service/src/services/auth_service.py`)

Add the model import near the top (after the `from src.models.user import ...` line):

```python
from src.models.organization import Organization
```

In `issue_tokens`, immediately before the `access_token = create_access_token(` call (line 155), load the org:

```python
    org = (
        await db.get(Organization, user.organization_id)
        if user.organization_id
        else None
    )
```

and add these three keyword args to that `create_access_token(...)` call (after `groups=group_ids,`):

```python
        org_id=str(org.id) if org else None,
        org_slug=org.slug if org else None,
        org_is_public=org.is_public if org else False,
```

In `rotate_refresh_token`, immediately before the `new_access = create_access_token(` call (line 252), load the org the same way:

```python
    org = (
        await db.get(Organization, user.organization_id)
        if user.organization_id
        else None
    )
```

and add the same three keyword args (after `groups=group_ids,`) to that `create_access_token(...)` call:

```python
        org_id=str(org.id) if org else None,
        org_slug=org.slug if org else None,
        org_is_public=org.is_public if org else False,
```

- [ ] **Step 6: Run the broader token/auth tests to verify nothing regressed**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest tests/test_org_token_claims.py tests/test_access_jti_binding.py -v`
Expected: PASS (no `TypeError` from the new required params; org tests green).

- [ ] **Step 7: Commit**

```bash
cd /Users/sidx/workspace/identity-service
git add service/src/auth/jwt.py service/src/services/auth_service.py service/tests/test_org_token_claims.py
git commit -m "feat(jwt): add oid/oslug/opub claims to access tokens

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Authz-token org claims

**Files:**
- Modify: `service/src/auth/jwt.py:90-122`
- Modify: `service/src/api/authz_routes.py:290-314`
- Test: `service/tests/test_org_token_claims.py` (append)

- [ ] **Step 1: Write the failing test** (append to `service/tests/test_org_token_claims.py`)

```python
from src.auth.jwt import _AUD_AUTHZ, create_authz_token


def test_authz_token_carries_org_claims():
    org_id = uuid.uuid4()
    token = create_authz_token(
        user_id=uuid.uuid4(),
        idp_sub="google|123",
        workspace_id=uuid.uuid4(),
        workspace_slug="acme",
        workspace_role="editor",
        actions=["read"],
        service_name="notes",
        org_id=str(org_id),
        org_slug="tamu",
        org_is_public=False,
    )
    payload = decode_token(token, audience=_AUD_AUTHZ)
    assert payload["oid"] == str(org_id)
    assert payload["oslug"] == "tamu"
    assert payload["opub"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest tests/test_org_token_claims.py::test_authz_token_carries_org_claims -v`
Expected: FAIL with `TypeError: create_authz_token() got an unexpected keyword argument 'org_id'`.

- [ ] **Step 3: Add org params + claims to `create_authz_token`** (`service/src/auth/jwt.py`)

Change the signature (lines 90-98) to add three params after `service_name`:

```python
def create_authz_token(
    user_id: uuid.UUID,
    idp_sub: str,
    workspace_id: uuid.UUID,
    workspace_slug: str,
    workspace_role: str,
    actions: list[str],
    service_name: str,
    org_id: str | None,
    org_slug: str | None,
    org_is_public: bool,
) -> str:
```

Add the claims to the payload dict, right after the `"actions": actions,` line (line 116):

```python
        "oid": org_id,
        "oslug": org_slug,
        "opub": org_is_public,
```

- [ ] **Step 4: Thread the org into the authz route** (`service/src/api/authz_routes.py`)

Add the model import near the other model imports at the top of the file:

```python
from src.models.organization import Organization
```

Immediately after `workspace = await db.get(Workspace, body.workspace_id)` (line 290), load the org:

```python
    org = (
        await db.get(Organization, user.organization_id)
        if user.organization_id
        else None
    )
```

Add these three keyword args to the `create_authz_token(...)` call (after `service_name=service_ctx.service_name,`, line 313):

```python
        org_id=str(org.id) if org else None,
        org_slug=org.slug if org else None,
        org_is_public=org.is_public if org else False,
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest tests/test_org_token_claims.py tests/test_authz_jwt.py -v`
Expected: PASS (org claim test + existing authz tests green).

- [ ] **Step 6: Commit**

```bash
cd /Users/sidx/workspace/identity-service
git add service/src/auth/jwt.py service/src/api/authz_routes.py service/tests/test_org_token_claims.py
git commit -m "feat(jwt): add oid/oslug/opub claims to authz tokens

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Workspace org-enforcement

Wire `workspace_allows_org` into the two issuance paths and the invite path. The decision logic is already unit-tested (Task 4); this task adds the call sites + a focused invite test.

**Files:**
- Modify: `service/src/services/auth_service.py` (`issue_tokens` ~141, `rotate_refresh_token` ~232)
- Modify: `service/src/services/workspace_service.py:108-129`
- Test: `service/tests/test_org_workspace_enforcement.py`

- [ ] **Step 1: Write the failing test** (create `service/tests/test_org_workspace_enforcement.py`)

```python
"""invite_member must reject users whose org is not allowed by the workspace."""

import uuid

import pytest

from src.models.user import User
from src.services import workspace_service


class _ScalarResult:
    def __init__(self, value=None, scalars=None):
        self._value = value
        self._scalars = scalars or []

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        parent = self

        class _S:
            def all(self_inner):
                return parent._scalars

        return _S()


class _FakeSession:
    def __init__(self, exec_results):
        self._exec = list(exec_results)
        self.added = []
        self.committed = False

    async def execute(self, _stmt):
        return self._exec.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True


def _user_with_org(org_id):
    u = User(email="x@tamu.edu", name="X")
    u.id = uuid.uuid4()
    u.organization_id = org_id
    return u


@pytest.mark.asyncio
async def test_invite_rejected_when_org_not_allowed():
    user = _user_with_org(uuid.uuid4())
    allowed_org = uuid.uuid4()  # different from the user's org
    session = _FakeSession(
        exec_results=[
            _ScalarResult(value=user),            # select User by email
            _ScalarResult(scalars=[allowed_org]),  # workspace_allows_org query
        ]
    )
    with pytest.raises(ValueError, match="organization is not permitted"):
        await workspace_service.invite_member(
            session, uuid.uuid4(), "x@tamu.edu", role="viewer"
        )
    assert session.committed is False


@pytest.mark.asyncio
async def test_invite_allowed_when_workspace_open():
    org_id = uuid.uuid4()
    user = _user_with_org(org_id)
    session = _FakeSession(
        exec_results=[
            _ScalarResult(value=user),       # select User by email
            _ScalarResult(scalars=[]),       # no allowed-org rows => open
        ]
    )
    membership = await workspace_service.invite_member(
        session, uuid.uuid4(), "x@tamu.edu", role="viewer"
    )
    assert membership.user_id == user.id
    assert session.committed is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest tests/test_org_workspace_enforcement.py -v`
Expected: FAIL (no org check yet — `test_invite_rejected_when_org_not_allowed` does not raise).

- [ ] **Step 3: Enforce in `invite_member`** (`service/src/services/workspace_service.py`)

Add the import near the top (after `from src.services import token_service`):

```python
from src.services import organization_service
```

In `invite_member`, after the user-not-found check (line 122, `raise ValueError("User not found")`) and before constructing the `WorkspaceMembership` (line 124), insert:

```python
    if not await organization_service.workspace_allows_org(
        db, workspace_id, user.organization_id
    ):
        raise ValueError("User's organization is not permitted in this workspace")
```

- [ ] **Step 4: Enforce in `issue_tokens`** (`service/src/services/auth_service.py`)

Add the import near the top (after `from src.services import token_service`):

```python
from src.services import organization_service
```

In `issue_tokens`, right after the membership check (line 141, `raise ValueError("User is not a member of this workspace")`), insert:

```python
    if not await organization_service.workspace_allows_org(
        db, workspace_id, user.organization_id
    ):
        raise ValueError("User's organization is not permitted in this workspace")
```

- [ ] **Step 5: Enforce in `rotate_refresh_token`** (`service/src/services/auth_service.py`)

In `rotate_refresh_token`, right after the membership re-check that raises `"User is no longer a member of this workspace"` (line 232), insert (note: revoke the family so a now-disallowed org can't keep refreshing):

```python
    if not await organization_service.workspace_allows_org(
        db, workspace_id, user.organization_id
    ):
        await token_service.revoke_token_family(family_id)
        raise ValueError("User's organization is not permitted in this workspace")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest tests/test_org_workspace_enforcement.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: Commit**

```bash
cd /Users/sidx/workspace/identity-service
git add service/src/services/workspace_service.py service/src/services/auth_service.py service/tests/test_org_workspace_enforcement.py
git commit -m "feat(org): enforce workspace allowed-orgs at invite + token issuance

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Migration — tables, column, seed public org, backfill

**Files:**
- Create: `service/migrations/versions/<rev>_add_organizations.py` (generated, then filled in)

- [ ] **Step 1: Generate an empty revision** (wires `down_revision` correctly)

Run:
```bash
cd /Users/sidx/workspace/identity-service/service && uv run alembic revision -m "add organizations and email-domain tenancy"
```
Expected: prints `Generating .../migrations/versions/<rev>_add_organizations_and_email_domain_tenancy.py ... done`. Note the new file path.

- [ ] **Step 2: Replace the generated `upgrade()`/`downgrade()` bodies**

Open the new file and replace its `upgrade()` and `downgrade()` functions with the following (leave the auto-generated `revision`/`down_revision` header untouched):

```python
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
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
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
    op.drop_index(
        "ix_organization_domains_org_id", table_name="organization_domains"
    )
    op.drop_table("organization_domains")
    op.drop_index("uq_one_public_org", table_name="organizations")
    op.drop_table("organizations")
```

Confirm the file's imports include `from alembic import op` and `import sqlalchemy as sa` (Alembic's template adds these by default).

- [ ] **Step 3: Apply the migration**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run alembic upgrade head`
Expected: `Running upgrade 21bddf454fbc -> <rev>, add organizations and email-domain tenancy` with no errors.

- [ ] **Step 4: Verify the seed + backfill landed**

Run:
```bash
cd /Users/sidx/workspace/identity-service/service && uv run python -c "
import asyncio
from sqlalchemy import text
from src.database import engine
async def main():
    async with engine.connect() as c:
        org = (await c.execute(text(\"select slug, is_public, enabled from organizations where is_public\"))).all()
        nulls = (await c.execute(text('select count(*) from users where organization_id is null'))).scalar_one()
        print('public org:', org)
        print('users still without org:', nulls)
asyncio.run(main())
"
```
Expected: `public org: [('public', True, True)]` and `users still without org: 0`.

- [ ] **Step 5: Verify the downgrade is clean, then re-upgrade**

Run:
```bash
cd /Users/sidx/workspace/identity-service/service && uv run alembic downgrade -1 && uv run alembic upgrade head
```
Expected: downgrade drops the tables/column without error, then upgrade re-applies cleanly.

- [ ] **Step 6: Commit**

```bash
cd /Users/sidx/workspace/identity-service
git add service/migrations/versions/
git commit -m "feat(migration): organizations tables, users.organization_id, seed public org

Creates organizations / organization_domains / workspace_allowed_organizations,
adds users.organization_id, seeds the public org (enabled), and backfills all
existing users into it for a zero-lockout deploy.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Full suite + lint gate

**Files:** none (verification only)

- [ ] **Step 1: Run the full service test suite**

Run: `cd /Users/sidx/workspace/identity-service/service && uv run pytest -v`
Expected: all tests PASS (the new org tests + all pre-existing tests; no regressions from the new required token params or the `find_or_create_user` signature change).

- [ ] **Step 2: Run lint/format**

Run: `cd /Users/sidx/workspace/identity-service && make lint`
Expected: clean. If it flags formatting, run `make fmt` and re-run `make lint`, then amend the relevant commit or add a `style:` commit.

- [ ] **Step 3: Final verification note**

Confirm by re-reading the diff that every spec checkpoint is wired:
- Sign-in 403 when `resolve_organization` returns `None` (Task 5).
- `oid`/`oslug`/`opub` on access (Task 6) + authz (Task 7) tokens.
- Allowed-orgs enforced at invite + issuance + refresh (Task 8).
- Public org seeded + users backfilled (Task 9).

---

## Self-Review

**Spec coverage:**
- §1 Data model (3 tables + column) → Tasks 1, 2, 9. ✓
- §2 Org resolution (normalize, exact, longest-subdomain, public fallback) → Tasks 3, 4. ✓
- §3 Sign-in gate (403, no user row; re-resolved each login) → Task 5. ✓
- §4 Token claims (access + authz; `opub`) → Tasks 6, 7. ✓
- §5 Workspace enforcement (invite + issuance; refresh too) → Task 8. ✓
- §7 Migration (seed public, backfill, zero lockout) → Task 9. ✓
- §8 Security (strict normalization, fail-closed) → Task 3 (normalizer + anti-spoof tests). ✓
- **Deferred to later plans (noted in Scope):** admin CRUD API + React UI (Plan 2); SDK org-claim exposure (Plan 3); Google `hd` / EntraID `tid` authoritative assurance (spec Future work).

**Placeholder scan:** none — every code step is complete and paste-ready.

**Type consistency:** `create_access_token` / `create_authz_token` gain identical `org_id: str | None, org_slug: str | None, org_is_public: bool` params; callers in `auth_service.py` and `authz_routes.py` pass `str(org.id) if org else None`, `org.slug if org else None`, `org.is_public if org else False`. `resolve_organization` returns `Organization | None`; the callback checks `None`. `workspace_allows_org(db, workspace_id, organization_id|None) -> bool` is called identically in all three enforcement sites. `find_or_create_user` gains required `organization_id: uuid.UUID`; the single caller (callback) and both existing tests pass it.
