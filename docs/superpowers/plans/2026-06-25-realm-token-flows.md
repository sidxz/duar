# Realm Token Flows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the no-user realm token primitive and the two service-facing realm endpoints — `GET /realm/whoami` (SDK scope self-discovery) and `POST /realm/m2m-token` (mint a short-lived `sentinel:m2m` token) — so a realm member can credential a background, no-human app↔app call entirely through Duar.

**Architecture:** A new `sentinel:m2m` JWT audience + `create_m2m_token()` mints a token carrying **service identity only** (no `sub`/user claims). A new `/realm` router (service-key-only) exposes `whoami` (returns `service_name`, `effective_scope`, and the realm if any) and `m2m-token` (server-stamps `caller`/`svc` from the authenticated key, mints under the realm's `m2m_ttl_s`, rejects non-members and inactive realms). The token's `svc` is the realm slug (`effective_scope`), so any member accepts it; receiver-side acceptance lives in the SDK (Plan 5).

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, PyJWT (RS256, `kid` headers), slowapi, pytest + pytest-asyncio (managed by `uv`).

## Scope boundary (this is Plan 2 of 6)

1. Realm scope core — **DONE** (`effective_scope`, realm-scoped permissions + authz minting).
2. **Token flows** ← this plan. **Service-side only:** `_AUD_M2M`, `create_m2m_token()`, `GET /realm/whoami`, `POST /realm/m2m-token`.
3. Network split — `create_app(tier)`, unpublished internal listener (registers + mounts the `/realm` router on the internal listener).
4. Admin — `/admin/realms` CRUD + membership, React Realms page.
5. **SDKs** — **ALL** SDK m2m work lands here: Python `whoami` scope-discovery (cached), broaden authz `svc` check to `effective_scope`, `mint_m2m_token()` auto-refresh helper, `SystemAuth` accept (`type=m2m`); JS m2m mint+accept (server entry only). Receiver-side m2m **acceptance is NOT in this plan** — Plan 2 only mints and self-describes.
6. Docs + integration tests.

After this plan: a service with a realm member key can call `GET /realm/whoami` to learn its `effective_scope`, and `POST /realm/m2m-token` to obtain a signed no-user realm token (verified by unit + behavioral tests). End-to-end Flow B (App B *accepts* the token) is proven once Plan 5 adds SDK acceptance.

## Global Constraints

- Python 3.12; run everything via `uv` (`cd service && uv run ...`).
- Tests use **pure unit style with fakes** OR the **behavioral TestClient + `dependency_overrides` + monkeypatch** house style (model on `service/tests/test_realm_authz_minting.py`). No real DB/Redis; no `conftest.py`; tests import from `src.*` directly (pytest `pythonpath = ["."]`). Mark async unit tests with `@pytest.mark.asyncio`.
- Lint/format with ruff, **changed files only**: `cd service && uv run ruff format <changed files> && uv run ruff check --fix <changed files>`. NEVER `ruff format .` / whole-tree `--fix` / `make fmt` — they reformat unrelated **uncommitted** files.
- **Stage only the files each task lists** (`git add <those paths>`); never `git add -A` / `git add .`.
- **Never modify** `service/src/services/role_service.py` or `service/tests/test_register_actions.py`, and do not touch the user's other uncommitted edits (`config.py`, `main.py`, `middleware/rate_limit.py`, `permission_routes.py`, `test_rate_limit_*`, `test_ratelimit_event`). **This plan does NOT modify `main.py`** — the realm router is registered in Plan 3 (network split), so `main.py` (which carries the user's uncommitted rate-limit edits) is never staged here.
- **Do NOT add new settings to `config.py`** — it has uncommitted user edits. Reuse the existing `settings.rate_limit_authz_resolve` tier for `/realm/m2m-token` (same per-service service-to-service mint semantics as `/authz/resolve`).
- Test gate = the task's **own** test file (always runnable). The broad suite may show IdP/JWKS connection failures under the network-restricted sandbox — those are environmental, not task failures.
- Branch: `realm-trusted-app-group` (already checked out). Commit after every task.
- **Non-breaking invariant:** a standalone service (`realm_slug is None`) cannot mint an m2m token (no shared scope) and its `whoami` reports `realm: null`, `effective_scope == service_name`. No existing endpoint changes behavior.
- **SDD execution housekeeping (do before dispatching Task 1):** archive Plan 1's `.superpowers/sdd/progress.md` (e.g. to `progress-plan1-archive.md`), start a fresh ledger, and **regenerate each `task-N-brief` from THIS plan file** — the brief slots may hold stale Plan-1/rate-limit content.

---

### Task 1: `sentinel:m2m` audience + `create_m2m_token()`

**Files:**
- Modify: `service/src/auth/jwt.py` (add `_AUD_M2M` constant near `:23-26`; add `create_m2m_token()` after `create_authz_token`)
- Test: `service/tests/test_m2m_token.py`

**Interfaces:**
- Consumes: `_sign`, `_ISSUER`, `decode_token` (existing in `jwt.py`).
- Produces: `_AUD_M2M = "sentinel:m2m"`; `create_m2m_token(svc: str, caller: str, ttl_s: int, actions: list[str] | None = None, aud_target: str | None = None) -> str`. Token claims: `iss, aud=_AUD_M2M, type="m2m", svc, caller, actions (default ["*"]), aud_target, jti, iat, exp=iat+ttl_s`. **No** `sub`/`email`/`idp_sub`/user claims.

- [ ] **Step 1: Write the failing test**

```python
# service/tests/test_m2m_token.py
"""The no-user realm m2m token: service identity only, sentinel:m2m audience."""

import pytest

from src.auth.jwt import _AUD_ACCESS, _AUD_M2M, create_m2m_token, decode_token


def test_m2m_token_carries_service_identity_no_user():
    token = create_m2m_token(svc="acme-suite", caller="docs", ttl_s=300)
    payload = decode_token(token, audience=_AUD_M2M)
    assert payload["type"] == "m2m"
    assert payload["svc"] == "acme-suite"
    assert payload["caller"] == "docs"
    assert payload["actions"] == ["*"]
    assert payload["aud_target"] is None
    # An honest "no human" token: zero user/identity claims.
    assert "sub" not in payload
    assert "email" not in payload
    assert "idp_sub" not in payload


def test_m2m_token_exp_honors_ttl():
    token = create_m2m_token(svc="acme-suite", caller="docs", ttl_s=120)
    payload = decode_token(token, audience=_AUD_M2M)
    assert payload["exp"] - payload["iat"] == 120


def test_m2m_token_audience_is_distinct_from_access():
    """Audience separation defends against token-type confusion: a no-user m2m
    token must NOT validate as a user access token."""
    token = create_m2m_token(svc="acme-suite", caller="docs", ttl_s=300)
    with pytest.raises(Exception):
        decode_token(token, audience=_AUD_ACCESS)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_m2m_token.py -v`
Expected: FAIL with `ImportError: cannot import name '_AUD_M2M'` / `create_m2m_token`.

- [ ] **Step 3: Add the `_AUD_M2M` constant**

In `service/src/auth/jwt.py`, add immediately after `_AUD_AUTHZ = "sentinel:authz"` (line 26):

```python
_AUD_M2M = "sentinel:m2m"
```

- [ ] **Step 4: Add `create_m2m_token()`**

In `service/src/auth/jwt.py`, add this function right after `create_authz_token` (after line 134, before `decode_token`):

```python
def create_m2m_token(
    svc: str,
    caller: str,
    ttl_s: int,
    actions: list[str] | None = None,
    aud_target: str | None = None,
) -> str:
    """Create a short-lived no-user (machine-to-machine) realm token.

    Carries SERVICE identity only — no ``sub``/``email``/``idp_sub`` — so it can
    never be mistaken for a human. ``svc`` is the realm slug (the shared scope a
    receiving member checks against); ``caller`` is the minting member's
    ``service_name``, server-stamped for audit. ``actions=["*"]`` is full in-realm
    trust in v1; a narrowed list is enforceable later with no token-shape change.
    ``aud_target`` is reserved (off by default) for future per-call narrowing —
    when set, the receiver checks it equals its own ``service_name``.
    """
    now = datetime.now(UTC)
    payload = {
        "iss": _ISSUER,
        "aud": _AUD_M2M,
        "type": "m2m",
        "svc": svc,
        "caller": caller,
        "actions": actions if actions is not None else ["*"],
        "aud_target": aud_target,
        "jti": str(uuid.uuid4()),  # enables future denylist-based hard revoke
        "iat": now,
        "exp": now + timedelta(seconds=ttl_s),
    }
    return _sign(payload)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd service && uv run pytest tests/test_m2m_token.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Lint + commit**

```bash
cd service && uv run ruff format $FILES && uv run ruff check --fix $FILES   # $FILES = ONLY this task's files; never 'ruff format .' / 'make fmt'
git add service/src/auth/jwt.py service/tests/test_m2m_token.py
git commit -m "feat(realm): sentinel:m2m audience + create_m2m_token (no-user token)"
```

---

### Task 2: Realm response schemas + `get_realm_by_slug`

**Files:**
- Create: `service/src/schemas/realm.py`
- Modify: `service/src/services/realm_service.py` (add `get_realm_by_slug`)
- Test: `service/tests/test_realm_lookup.py`

**Interfaces:**
- Consumes: `Realm` model (Plan 1).
- Produces:
  - `realm_service.get_realm_by_slug(db, slug: str) -> Realm | None`.
  - Schemas: `RealmInfo(slug, name)`; `WhoamiResponse(service_name, effective_scope, realm: RealmInfo | None = None)`; `M2MTokenRequest(target: str | None = None)`; `M2MTokenResponse(token, expires_in)`.

- [ ] **Step 1: Write the failing test**

```python
# service/tests/test_realm_lookup.py
"""get_realm_by_slug resolves a realm by its shared-scope slug; realm schemas."""

import uuid

import pytest

from src.models.realm import Realm


class _Result:
    def __init__(self, realm):
        self._realm = realm

    def scalar_one_or_none(self):
        return self._realm


class _FakeDB:
    def __init__(self, realm):
        self._realm = realm

    async def execute(self, _stmt):
        return _Result(self._realm)


@pytest.mark.asyncio
async def test_get_realm_by_slug_found():
    from src.services import realm_service

    realm = Realm(id=uuid.uuid4(), name="Acme Suite", slug="acme-suite", m2m_ttl_s=300)
    out = await realm_service.get_realm_by_slug(_FakeDB(realm), "acme-suite")
    assert out is realm


@pytest.mark.asyncio
async def test_get_realm_by_slug_missing_returns_none():
    from src.services import realm_service

    out = await realm_service.get_realm_by_slug(_FakeDB(None), "nope")
    assert out is None


def test_whoami_response_standalone_has_null_realm():
    from src.schemas.realm import WhoamiResponse

    r = WhoamiResponse(service_name="docs", effective_scope="docs", realm=None)
    assert r.realm is None
    assert r.effective_scope == "docs"


def test_whoami_response_member_carries_realm_info():
    from src.schemas.realm import RealmInfo, WhoamiResponse

    r = WhoamiResponse(
        service_name="docs",
        effective_scope="acme-suite",
        realm=RealmInfo(slug="acme-suite", name="Acme Suite"),
    )
    assert r.realm.slug == "acme-suite"
    assert r.effective_scope == "acme-suite"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_realm_lookup.py -v`
Expected: FAIL — `AttributeError: module 'src.services.realm_service' has no attribute 'get_realm_by_slug'` and `ModuleNotFoundError: No module named 'src.schemas.realm'`.

- [ ] **Step 3: Add `get_realm_by_slug` to `realm_service`**

In `service/src/services/realm_service.py`, add after `get_realm` (after line 34):

```python
async def get_realm_by_slug(db: AsyncSession, slug: str) -> Realm | None:
    """Resolve a realm by its shared-scope slug. Used by /realm/whoami (for the
    display name) and /realm/m2m-token (for is_active + m2m_ttl_s)."""
    result = await db.execute(select(Realm).where(Realm.slug == slug))
    return result.scalar_one_or_none()
```

(`select` and `Realm` are already imported at the top of the file.)

- [ ] **Step 4: Create the response schemas**

```python
# service/src/schemas/realm.py
"""Schemas for realm self-discovery (whoami) and no-user m2m token minting."""

from pydantic import BaseModel, Field


class RealmInfo(BaseModel):
    slug: str
    name: str


class WhoamiResponse(BaseModel):
    """What a service key resolves to: its own name, the shared ``effective_scope``
    it reads/writes permissions under, and its realm (null when standalone)."""

    service_name: str
    effective_scope: str
    realm: RealmInfo | None = None


class M2MTokenRequest(BaseModel):
    target: str | None = Field(
        default=None,
        description=(
            "Reserved for future per-call audience narrowing — NOT enforced in v1. "
            "When honored, restricts the token to a single target service_name."
        ),
    )


class M2MTokenResponse(BaseModel):
    token: str
    expires_in: int
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd service && uv run pytest tests/test_realm_lookup.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Lint + commit**

```bash
cd service && uv run ruff format $FILES && uv run ruff check --fix $FILES   # $FILES = ONLY this task's files; never 'ruff format .' / 'make fmt'
git add service/src/schemas/realm.py service/src/services/realm_service.py \
  service/tests/test_realm_lookup.py
git commit -m "feat(realm): realm response schemas + get_realm_by_slug lookup"
```

---

### Task 3: `/realm` router — `whoami` + `m2m-token`

**Files:**
- Create: `service/src/api/realm_routes.py`
- Test: `service/tests/test_realm_routes.py`

> **Registration deferred to Plan 3.** This task does **NOT** modify `service/src/main.py`. The realm router belongs on the unpublished *internal* listener, and Plan 3's `create_app(tier)` factory is where every router is assigned to a tier — registering it on today's monolithic app would be churn Plan 3 immediately rewrites. The behavioral test below builds its own FastAPI app and includes the router directly, so this task is fully testable without touching `main.py`. (Bonus: `main.py` currently carries the user's unrelated uncommitted rate-limit edits, which must not be folded into a realm commit — not touching the file sidesteps that entirely.)

**Interfaces:**
- Consumes: `create_m2m_token`, `_AUD_M2M` (Task 1); `RealmInfo`, `WhoamiResponse`, `M2MTokenRequest`, `M2MTokenResponse`, `get_realm_by_slug` (Task 2); `ServiceKeyContext`, `require_service_key` (Plan 1, `dependencies.py`); `limiter`, `service_or_ip_key` (`middleware/rate_limit.py`); `log_security` (`logging_events`).
- Produces: `router` (prefix `/realm`) with `GET /realm/whoami` and `POST /realm/m2m-token`. **Not yet mounted on the running app** — Plan 3 mounts it on the internal listener.

- [ ] **Step 1: Write the failing test**

```python
# service/tests/test_realm_routes.py
"""Behavioral tests for /realm/whoami + /realm/m2m-token.

Drives the real handlers through a TestClient with the service-key dependency and
the realm lookup mocked — the house behavioral style (see test_realm_authz_minting).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded

from src.api import realm_routes
from src.api.dependencies import ServiceKeyContext, require_service_key
from src.api.realm_routes import router as realm_router
from src.auth.jwt import _AUD_M2M, decode_token
from src.database import get_db
from src.middleware.rate_limit import limiter, rate_limit_exceeded_handler


@pytest.fixture(autouse=True)
def _disable_limiter():
    original = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = original


class _Realm:
    def __init__(
        self, *, slug="acme-suite", name="Acme Suite", m2m_ttl_s=300, is_active=True
    ):
        self.slug = slug
        self.name = name
        self.m2m_ttl_s = m2m_ttl_s
        self.is_active = is_active


def _build_app(*, realm_slug):
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(realm_router)
    app.dependency_overrides[require_service_key] = lambda: ServiceKeyContext(
        service_name="docs", realm_slug=realm_slug
    )

    async def _db():
        yield None

    app.dependency_overrides[get_db] = _db
    return app


def test_whoami_member_returns_realm_and_scope(monkeypatch):
    async def _get(_db, slug):
        return _Realm(slug=slug, name="Acme Suite")

    monkeypatch.setattr(realm_routes.realm_service, "get_realm_by_slug", _get)
    resp = TestClient(_build_app(realm_slug="acme-suite")).get("/realm/whoami")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service_name"] == "docs"
    assert body["effective_scope"] == "acme-suite"
    assert body["realm"] == {"slug": "acme-suite", "name": "Acme Suite"}


def test_whoami_standalone_has_null_realm():
    resp = TestClient(_build_app(realm_slug=None)).get("/realm/whoami")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service_name"] == "docs"
    assert body["effective_scope"] == "docs"
    assert body["realm"] is None


def test_m2m_mint_stamps_caller_and_realm_svc(monkeypatch):
    async def _get(_db, slug):
        return _Realm(slug=slug, m2m_ttl_s=300, is_active=True)

    monkeypatch.setattr(realm_routes.realm_service, "get_realm_by_slug", _get)
    monkeypatch.setattr(realm_routes, "log_security", lambda *a, **k: None)
    resp = TestClient(_build_app(realm_slug="acme-suite")).post(
        "/realm/m2m-token", json={}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["expires_in"] == 300
    payload = decode_token(body["token"], audience=_AUD_M2M)
    assert payload["type"] == "m2m"
    assert payload["svc"] == "acme-suite"  # shared realm scope (any member accepts)
    assert payload["caller"] == "docs"  # server-stamped minter, not client-asserted
    assert payload["actions"] == ["*"]
    assert "sub" not in payload


def test_m2m_mint_rejects_standalone(monkeypatch):
    monkeypatch.setattr(realm_routes, "log_security", lambda *a, **k: None)
    resp = TestClient(_build_app(realm_slug=None)).post("/realm/m2m-token", json={})
    assert resp.status_code == 403


def test_m2m_mint_rejects_inactive_realm(monkeypatch):
    async def _get(_db, slug):
        return _Realm(slug=slug, is_active=False)

    monkeypatch.setattr(realm_routes.realm_service, "get_realm_by_slug", _get)
    monkeypatch.setattr(realm_routes, "log_security", lambda *a, **k: None)
    resp = TestClient(_build_app(realm_slug="acme-suite")).post(
        "/realm/m2m-token", json={}
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_realm_routes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.api.realm_routes'`.

- [ ] **Step 3: Create the `/realm` router**

```python
# service/src/api/realm_routes.py
"""Realm self-discovery + no-user (m2m) token minting.

Both endpoints require a service key (``require_service_key``). ``whoami`` lets the
SDK self-discover its shared ``effective_scope`` with no app-side config.
``m2m-token`` mints a short-lived no-user realm token: the caller proves itself with
its service key, Duar server-stamps the token's ``caller``/``svc`` from that key
(never client-asserted), so a leaked key can only mint its own member's token — it
cannot impersonate another member or jump realms.

(Plan 3 moves this router onto the unpublished internal listener.)
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import ServiceKeyContext, require_service_key
from src.auth.jwt import create_m2m_token
from src.config import settings
from src.database import get_db
from src.logging_events import log_security
from src.middleware.rate_limit import limiter, service_or_ip_key
from src.schemas.realm import (
    M2MTokenRequest,
    M2MTokenResponse,
    RealmInfo,
    WhoamiResponse,
)
from src.services import realm_service

logger = structlog.get_logger()

router = APIRouter(prefix="/realm", tags=["realm"])


@router.get("/whoami", response_model=WhoamiResponse)
async def whoami(
    svc: ServiceKeyContext = Depends(require_service_key),
    db: AsyncSession = Depends(get_db),
):
    """Resolve the calling service key to its shared scope. The SDK caches this so
    apps carry no realm config: standalone → effective_scope is the service_name,
    realm member → effective_scope is the realm slug (+ realm name for display)."""
    realm_info: RealmInfo | None = None
    if svc.realm_slug:
        realm = await realm_service.get_realm_by_slug(db, svc.realm_slug)
        if realm is not None:
            realm_info = RealmInfo(slug=realm.slug, name=realm.name)
    return WhoamiResponse(
        service_name=svc.service_name,
        effective_scope=svc.effective_scope,
        realm=realm_info,
    )


@router.post("/m2m-token", response_model=M2MTokenResponse)
@limiter.limit(settings.rate_limit_authz_resolve, key_func=service_or_ip_key)
async def mint_m2m_token(
    request: Request,
    body: M2MTokenRequest,
    svc: ServiceKeyContext = Depends(require_service_key),
    db: AsyncSession = Depends(get_db),
):
    """Mint a short-lived no-user realm token for an in-realm system call.

    Rejects unless the caller is an active member of an active realm — a standalone
    service has no shared scope to mint under. Identity is server-stamped from the
    authenticated key (caller=service_name, svc=realm slug), so a leaked key cannot
    impersonate another member or jump realms.
    """
    if not svc.realm_slug:
        raise HTTPException(
            status_code=403,
            detail="Service is not a realm member; no-user tokens require a realm",
        )
    realm = await realm_service.get_realm_by_slug(db, svc.realm_slug)
    if realm is None or not realm.is_active:
        raise HTTPException(
            status_code=403,
            detail="Realm is inactive or no longer exists",
        )

    token = create_m2m_token(
        svc=svc.effective_scope,  # realm slug — the shared audience every member checks
        caller=svc.service_name,  # server-stamped: which member minted it (audit)
        ttl_s=realm.m2m_ttl_s,
        # body.target is accepted for forward-compat but NOT honored in v1: per-call
        # aud_target narrowing is reserved future work, so we mint aud_target=None.
    )
    log_security(
        "realm.m2m.minted",
        outcome="success",
        caller_service=svc.service_name,
        realm=svc.realm_slug,
    )
    return M2MTokenResponse(token=token, expires_in=realm.m2m_ttl_s)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd service && uv run pytest tests/test_realm_routes.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Verify the router imports cleanly (no app registration)**

The router is not mounted on `main.py` yet (Plan 3 does that). Sanity-check that the module imports with no error, and that the existing realm/token tests still pass:

Run: `cd service && uv run python -c "import src.api.realm_routes; print('ok')"`
Expected: prints `ok` (no ImportError).

Run: `cd service && uv run pytest tests/test_realm_routes.py tests/test_m2m_token.py tests/test_realm_lookup.py -q`
Expected: PASS (all green). (A broad `pytest tests/` may show IdP/JWKS **connection** failures — the known network-sandbox artifact, not this task's failures.)

- [ ] **Step 6: Lint + commit (new files only — do NOT touch `main.py`)**

```bash
cd service && uv run ruff format $FILES && uv run ruff check --fix $FILES   # $FILES = ONLY this task's files; never 'ruff format .' / 'make fmt'
git add service/src/api/realm_routes.py service/tests/test_realm_routes.py   # NEVER add main.py / -A / .
git commit -m "feat(realm): /realm/whoami + /realm/m2m-token (mint no-user token)"
```

---

## Self-review (done by plan author)

**Spec coverage (Plan 2's slice):**
- `_AUD_M2M = "sentinel:m2m"`, kept separate from access/authz/admin/refresh (token-type-confusion defense) → Task 1 (+ `test_m2m_token_audience_is_distinct_from_access`).
- `create_m2m_token()` — service identity only, `actions=["*"]`, `aud_target` reserved-off, `exp = iat + ttl_s`, server-stampable `caller`/`svc` → Task 1.
- `GET /realm/whoami` — `require_service_key`; returns `{service_name, effective_scope, realm | null}`; SDK caches it → Task 3.
- `POST /realm/m2m-token` — `require_service_key`; rate-limited via `service_or_ip_key`; server-stamps `caller`+`svc`; mints under `realm.m2m_ttl_s`; rejects non-member + inactive realm → Task 3.
- New router `realm_routes.py`; `get_realm_by_slug` + response schemas → Tasks 2-3.
- **Authz-token `svc=effective_scope` minting was already done in Plan 1** (`/authz/resolve`) — not repeated here.
- **Deferred (out of Plan 2):** **mounting the `/realm` router on the running app (Plan 3** — `create_app(tier)` assigns it to the internal listener; not done here to avoid churn + to keep clear of the user's uncommitted `main.py` edits); receiver-side m2m acceptance + `SystemAuth` + SDK `whoami`/`mint_m2m_token()` (Plan 5); `/admin/realms` CRUD (Plan 4); `aud_target` enforcement (future work).

**Placeholder scan:** none — every code/command step carries full content.

**Type consistency:** `create_m2m_token(svc, caller, ttl_s, actions=None, aud_target=None)` defined in Task 1 and called with exactly `svc=`, `caller=`, `ttl_s=` in Task 3. `get_realm_by_slug(db, slug) -> Realm | None` defined in Task 2, called in Task 3's two handlers. Schemas `WhoamiResponse`/`RealmInfo`/`M2MTokenRequest`/`M2MTokenResponse` defined in Task 2, imported + used in Task 3. `_AUD_M2M`/`decode_token` consistent across Tasks 1 and 3 tests.

**Reused-not-added:** `/realm/m2m-token` reuses `settings.rate_limit_authz_resolve` (no new config setting — `config.py` is off-limits). `whoami` is undecorated (cheap, SDK-cached) so it falls under the default per-IP middleware tier — intentional.

**Known integration gaps (call out at execution):** `require_service_key`'s real DB/Redis path and the rate-limit decorator's Redis backend are exercised only at runtime (`make start`), not by the pure-unit/behavioral suite (limiter disabled, key dep overridden). End-to-end Flow B (App B accepts the minted token) lands with Plan 5's SDK acceptance.
