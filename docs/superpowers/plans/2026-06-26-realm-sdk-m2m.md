# Realm SDK m2m Implementation Plan (Plan 5 — Python + JS)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make realm Flow A (shared user-context) and Flow B (no-user m2m) work end-to-end **from the SDK side** — the receiver-side acceptance and sender-side minting the service endpoints (Plan 2) already expose but nothing consumes yet.

**Architecture:** The SDK self-discovers its shared scope from Duar via `GET /realm/whoami` (cached, no app-side realm config). That `effective_scope` (realm slug for a member, else the service's own name) is then substituted in three places: (1) the authz-token `svc` check broadens from `service_name` to `effective_scope` so a realm-shared user token validates on any member; (2) the `PermissionClient`/`RoleClient` send `effective_scope` so a member's permission/RBAC rows land in the shared namespace (the server's `verify_service_scope` rejects anything else); (3) a new no-user `SystemAuth` context accepts `type=m2m` tokens (`aud=sentinel:m2m`), and a `mint_m2m_token()` helper mints+caches them for outbound system calls. All trust is rooted in Duar's RS256 signature — never app↔app.

**Tech Stack:** Python SDK (`sdk/`, `duar_auth`): PyJWT, httpx, FastAPI/Starlette, pytest + pytest-asyncio + respx. JS SDKs (`sdks/js`, `sdks/nextjs`): TypeScript, `jose`, vitest, tsup.

## Scope boundary (this is Plan 5 of 6)

1. Realm scope core — **DONE** (`effective_scope`, realm-scoped permissions + authz minting, server side).
2. Token flows — **DONE** (`_AUD_M2M`, `create_m2m_token`, `GET /realm/whoami`, `POST /realm/m2m-token`, server side).
3. Network split — **DONE** (`create_app(tier)`, unpublished internal listener mounts `/realm`).
4. Admin — **DONE** (`/admin/realms` CRUD + membership, React Realms UI).
5. **SDKs ← this plan.** Python `duar_auth`: whoami discovery (cached); broaden authz `svc` check to `effective_scope`; **`PermissionClient`/`RoleClient` send `effective_scope`** (required — server rejects otherwise); `mint_m2m_token()` with ~80%-TTL auto-refresh; accept `type=m2m` → new `SystemAuth`. JS (`@duar-auth/js` server entry + `@duar-auth/nextjs`): whoami helper; broaden the nextjs middleware `svc` check to scope; m2m **mint + accept** in the **server entry only** — never browser.
6. Docs + integration tests — **next plan.**

After this plan: a Python or JS realm member can (a) accept a realm-shared user authz token, (b) read/write shared permissions + RBAC, (c) mint a no-user m2m token for an outbound system call, and (d) accept an inbound m2m token as a `SystemAuth`. End-to-end Flow B is real on both SDK sides.

## Global Constraints

- **Python:** 3.12; run via `uv` **from the SDK dir**: `cd sdk && uv run pytest`. Tests use the existing `sdk/tests/conftest.py` fixtures (`rsa_keypair → (private_pem, public_pem)`, `make_token`) + **respx** for httpx mocking + **pytest-asyncio** (auto mode — no marker needed for async tests in this package, but other SDK tests omit it, so omit it). Lint **changed files only**: `cd sdk && uv run ruff format <files> && uv run ruff check --fix <files>`. NEVER `ruff format .` / whole-tree `--fix` / `make fmt`.
- **JS:** run from each package dir. `@duar-auth/js`: `cd sdks/js && npm test` (vitest) + `npm run build` (tsup, typechecks). `@duar-auth/nextjs`: `cd sdks/nextjs && npm run build` (`npm test` is `--passWithNoTests`; the package has no unit harness — gate nextjs changes on `build`). If `node_modules` is missing, `npm install` first. Match existing JS style: **2-space indent, single quotes, no semicolons** (no formatter script exists).
- **Stage only the files each task lists** (`git add <those paths>`); never `git add -A` / `git add .`. Commit after every task.
- **Never modify** `service/src/services/role_service.py` or `service/tests/test_register_actions.py` (the user's uncommitted work). This plan touches **only** `sdk/` and `sdks/` — it never goes near `service/`, so this is naturally satisfied; do not stray.
- **Non-breaking invariant:** a standalone service (`/realm/whoami` returns `realm: null` → `effective_scope == service_name`) behaves exactly as today: authz `svc` check unchanged, `PermissionClient`/`RoleClient` send `service_name`, no m2m minting. A pre-realm Duar (no `/realm` endpoint → 404) must degrade gracefully to standalone, never crash.
- **React (`sdks/react`) is intentionally untouched** — it is browser-only with no server entry; m2m mint/accept must never reach a browser (spec). Do not add m2m to it.
- Branch: `realm-trusted-app-group` (already checked out).
- **SDD housekeeping (before dispatching Task A1):** archive the current `.superpowers/sdd/progress.md` (→ `progress-plan4backend-archive.md`), start a fresh ledger, and **regenerate each `task-N-brief` from THIS plan file** — the brief slots hold stale Plan-4 content otherwise. One implementer at a time.

---

## Part A — Python SDK (`sdk/`, `duar_auth`)

### Task A1: `SystemAuth` no-user context

**Files:**
- Modify: `sdk/src/duar_auth/auth.py` (add `SystemAuth` after `RequestAuth`)
- Modify: `sdk/src/duar_auth/__init__.py` (export `SystemAuth`)
- Test: `sdk/tests/test_system_auth.py`

**Interfaces:**
- Consumes: nothing (pure value type).
- Produces: `SystemAuth(caller: str, actions: list[str], svc: str)` — frozen dataclass; method `can(action: str) -> bool` returns `True` if `"*" in self.actions or action in self.actions`. This is the no-user counterpart to `RequestAuth`: it carries service identity only (the minting member's `caller`, the realm `svc`, and the granted `actions`), never a user.

- [ ] **Step 1: Write the failing test**

```python
# sdk/tests/test_system_auth.py
"""SystemAuth — the no-user (m2m) in-realm caller context."""

from duar_auth import SystemAuth


def test_full_trust_actions_star_allows_anything():
    sys_auth = SystemAuth(caller="docs", actions=["*"], svc="acme-suite")
    assert sys_auth.can("reports:export") is True
    assert sys_auth.can("anything") is True


def test_specific_action_allowed_and_denied():
    sys_auth = SystemAuth(caller="docs", actions=["reports:read"], svc="acme-suite")
    assert sys_auth.can("reports:read") is True
    assert sys_auth.can("reports:write") is False


def test_carries_caller_and_svc_no_user():
    sys_auth = SystemAuth(caller="docs", actions=["*"], svc="acme-suite")
    assert sys_auth.caller == "docs"
    assert sys_auth.svc == "acme-suite"
    # No user identity on a SystemAuth — it is an honest "no human" context.
    assert not hasattr(sys_auth, "user")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sdk && uv run pytest tests/test_system_auth.py -v`
Expected: FAIL — `ImportError: cannot import name 'SystemAuth' from 'duar_auth'`.

- [ ] **Step 3: Add `SystemAuth` to `auth.py`**

Append to `sdk/src/duar_auth/auth.py` (after the `RequestAuth` class):

```python
@dataclass(frozen=True)
class SystemAuth:
    """Per-request context for a no-user (machine-to-machine) in-realm call.

    The no-user counterpart to :class:`RequestAuth`. It is produced by
    ``Duar.verify_m2m_token`` after a ``type=m2m`` token (``aud=sentinel:m2m``)
    passes Duar's RS256 signature + realm-scope checks. It carries service
    identity only — never a user:

    - ``caller``: the realm member that minted the token (server-stamped, for audit).
    - ``svc``: the realm slug the token is scoped to (the shared ``effective_scope``).
    - ``actions``: granted actions. ``["*"]`` is full in-realm trust (v1); a narrowed
      list is honored by ``can`` with no shape change when least-privilege m2m ships.
    """

    caller: str
    actions: list[str]
    svc: str

    def can(self, action: str) -> bool:
        """Whether this system caller may perform ``action``. No network call."""
        return "*" in self.actions or action in self.actions
```

- [ ] **Step 4: Export it from the package**

In `sdk/src/duar_auth/__init__.py`, change the import line:

```python
from duar_auth.auth import RequestAuth, SystemAuth
```

and add `"SystemAuth",` to `__all__` (keep alphabetical — after `"DuarError",`):

```python
    "DuarError",
    "SystemAuth",
    "WorkspaceContext",
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd sdk && uv run pytest tests/test_system_auth.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Lint + commit**

```bash
cd sdk && uv run ruff format src/duar_auth/auth.py src/duar_auth/__init__.py tests/test_system_auth.py && uv run ruff check --fix src/duar_auth/auth.py src/duar_auth/__init__.py tests/test_system_auth.py
git add sdk/src/duar_auth/auth.py sdk/src/duar_auth/__init__.py sdk/tests/test_system_auth.py
git commit -m "feat(sdk): SystemAuth no-user (m2m) context"
```

---

### Task A2: whoami discovery + `effective_scope` + permission/role scope substitution

**Files:**
- Modify: `sdk/src/duar_auth/duar.py`
- Test: `sdk/tests/test_duar_whoami.py`

**Interfaces:**
- Consumes: `httpx` (already imported), `DuarError` (from `duar_auth.types`).
- Produces on `Duar`:
  - `effective_scope: str` property → `self._effective_scope or self.service_name`.
  - `realm: dict | None` property → the `{slug, name}` dict from whoami, or `None`.
  - `async fetch_whoami() -> dict | None` → GET `/realm/whoami` with `X-Service-Key`; sets `_effective_scope`/`_realm`; mutates already-created `permissions`/`roles` clients' `service_name`; tolerant of 404 / transport error (returns `None`, stays standalone).
  - The lazy `permissions`/`roles` clients are constructed with `service_name=self.effective_scope`.
  - `lifespan` calls `fetch_whoami()` (both modes) before registering actions.

- [ ] **Step 1: Write the failing test**

```python
# sdk/tests/test_duar_whoami.py
"""Duar.whoami: self-discovers effective_scope and routes clients under it."""

import httpx
import pytest
import respx

from duar_auth import Duar


def _duar() -> Duar:
    return Duar(
        base_url="https://duar.test",
        service_name="docs",
        service_key="svc-key",
        idp_public_key="-----BEGIN PUBLIC KEY-----\nx\n-----END PUBLIC KEY-----",
        idp_audience="my-client-id",
    )


@respx.mock
async def test_member_resolves_realm_scope_and_rewires_clients():
    respx.get("https://duar.test/realm/whoami").mock(
        return_value=httpx.Response(
            200,
            json={
                "service_name": "docs",
                "effective_scope": "acme-suite",
                "realm": {"slug": "acme-suite", "name": "Acme Suite"},
            },
        )
    )
    s = _duar()
    # Touch the clients BEFORE whoami so they are created with the bare name —
    # whoami must then mutate them in place (the get_auth factory captures them).
    assert s.permissions.service_name == "docs"
    assert s.roles.service_name == "docs"

    data = await s.fetch_whoami()

    assert data["effective_scope"] == "acme-suite"
    assert s.effective_scope == "acme-suite"
    assert s.realm == {"slug": "acme-suite", "name": "Acme Suite"}
    assert s.permissions.service_name == "acme-suite"
    assert s.roles.service_name == "acme-suite"


@respx.mock
async def test_standalone_stays_on_service_name():
    respx.get("https://duar.test/realm/whoami").mock(
        return_value=httpx.Response(
            200,
            json={"service_name": "docs", "effective_scope": "docs", "realm": None},
        )
    )
    s = _duar()
    await s.fetch_whoami()
    assert s.effective_scope == "docs"
    assert s.realm is None
    assert s.permissions.service_name == "docs"


@respx.mock
async def test_pre_realm_duar_404_degrades_to_standalone():
    respx.get("https://duar.test/realm/whoami").mock(
        return_value=httpx.Response(404, json={"detail": "Not Found"})
    )
    s = _duar()
    data = await s.fetch_whoami()
    assert data is None
    assert s.effective_scope == "docs"  # falls back to service_name, no crash
    assert s.realm is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sdk && uv run pytest tests/test_duar_whoami.py -v`
Expected: FAIL — `AttributeError: 'Duar' object has no attribute 'fetch_whoami'` (and `effective_scope`).

- [ ] **Step 3: Add scope state to `Duar.__init__`**

In `sdk/src/duar_auth/duar.py`, in `__init__`, after the existing
`self._duar_public_key: str | None = None` line, add:

```python
        self._effective_scope: str | None = None
        self._realm: dict | None = None
```

- [ ] **Step 4: Add the `effective_scope` / `realm` properties**

In `duar.py`, right after the `duar_public_key` property (before `# -- Lazy clients --`):

```python
    @property
    def effective_scope(self) -> str:
        """Shared scope this service reads/writes under: the realm slug when a
        member (discovered via ``fetch_whoami``), else the service's own name."""
        return self._effective_scope or self.service_name

    @property
    def realm(self) -> dict | None:
        """The realm this service belongs to (``{slug, name}``), or ``None`` if standalone."""
        return self._realm
```

- [ ] **Step 5: Route the lazy clients under `effective_scope`**

In `duar.py`, in the `permissions` property, change `service_name=self.service_name` to `service_name=self.effective_scope`. Do the same in the `roles` property. (Both currently read `self.service_name`.)

```python
    @property
    def permissions(self) -> PermissionClient:
        """Lazily-created permission client."""
        if self._permissions is None:
            self._permissions = PermissionClient(
                base_url=self.base_url,
                service_name=self.effective_scope,
                service_key=self.service_key,
                cache_ttl=self.cache_ttl,
            )
        return self._permissions

    @property
    def roles(self) -> RoleClient:
        """Lazily-created role client."""
        if self._roles is None:
            self._roles = RoleClient(
                base_url=self.base_url,
                service_name=self.effective_scope,
                service_key=self.service_key,
            )
        return self._roles
```

- [ ] **Step 6: Add `fetch_whoami()`**

In `duar.py`, add right after `fetch_duar_public_key`:

```python
    async def fetch_whoami(self) -> dict | None:
        """Self-discover the shared realm scope from Duar — no app-side config.

        Sets ``effective_scope`` to the realm slug when this service is a realm
        member, else leaves it as ``service_name``. Tolerant of a pre-realm Duar
        (``/realm`` absent → 404) or an unreachable internal listener: returns
        ``None`` and stays standalone, so older/partial deployments keep working.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self.base_url}/realm/whoami",
                    headers={"X-Service-Key": self.service_key},
                )
            if resp.status_code != 200:
                return None
            data = resp.json()
        except httpx.HTTPError:
            return None
        self._effective_scope = data.get("effective_scope")
        self._realm = data.get("realm")
        # Realm member: re-point any already-created permission/role clients at the
        # shared scope. The get_auth dependency factory captures these instances by
        # reference, so mutating .service_name in place updates that path too.
        if self._effective_scope:
            if self._permissions is not None:
                self._permissions.service_name = self._effective_scope
            if self._roles is not None:
                self._roles.service_name = self._effective_scope
        return data
```

- [ ] **Step 7: Call `fetch_whoami()` from `lifespan`**

In `duar.py`, in the `_lifespan` async generator, add the whoami call after the
authz-mode key fetch and before the actions registration:

```python
        @asynccontextmanager
        async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
            if self.mode == "authz":
                await self.fetch_duar_public_key()
            await self.fetch_whoami()
            if self.actions:
                await self.roles.register_actions(self.actions)
            yield
            ...
```

(Leave the shutdown/close block unchanged.)

- [ ] **Step 8: Run test to verify it passes**

Run: `cd sdk && uv run pytest tests/test_duar_whoami.py -v`
Expected: PASS (3 passed).

- [ ] **Step 9: Lint + commit**

```bash
cd sdk && uv run ruff format src/duar_auth/duar.py tests/test_duar_whoami.py && uv run ruff check --fix src/duar_auth/duar.py tests/test_duar_whoami.py
git add sdk/src/duar_auth/duar.py sdk/tests/test_duar_whoami.py
git commit -m "feat(sdk): whoami scope discovery + route permission/role clients under effective_scope"
```

---

### Task A3: Broaden the authz `svc` check to `effective_scope`

**Files:**
- Modify: `sdk/src/duar_auth/authz_middleware.py`
- Test: `sdk/tests/test_authz_effective_scope.py`

**Interfaces:**
- Consumes: the `Duar.effective_scope` property (Task A2) via `self._duar_instance`.
- Produces on `AuthzMiddleware`: an `effective_scope: str` property; the step-6 `svc` check compares `token_svc` against `self.effective_scope` instead of `self.service_name`.

- [ ] **Step 1: Write the failing test**

```python
# sdk/tests/test_authz_effective_scope.py
"""AuthzMiddleware accepts a realm-shared authz token (svc == effective_scope)."""

import datetime
import uuid

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from duar_auth.authz_middleware import AuthzMiddleware


@pytest.fixture(scope="module")
def keypairs():
    def _pair():
        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pub = k.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()
        return k, pub

    return {"idp": _pair(), "duar": _pair()}


class _FakeInstance:
    """Minimal stand-in for a Duar instance: only what the middleware reads."""

    idp_jwks_url = None

    def __init__(self, effective_scope: str):
        self.effective_scope = effective_scope


def _tokens(keypairs, *, svc: str):
    idp_priv, _ = keypairs["idp"]
    duar_priv, _ = keypairs["duar"]
    now = datetime.datetime.now(datetime.UTC)
    idp_sub = "google|123"
    idp_token = pyjwt.encode(
        {"sub": idp_sub, "aud": "client-id", "email": "a@b.com", "name": "A",
         "iat": now, "exp": now + datetime.timedelta(hours=1)},
        idp_priv, algorithm="RS256",
    )
    authz_token = pyjwt.encode(
        {"sub": str(uuid.uuid4()), "idp_sub": idp_sub, "svc": svc,
         "wid": str(uuid.uuid4()), "wslug": "acme", "wrole": "editor",
         "actions": ["read"], "aud": "sentinel:authz",
         "iat": now, "exp": now + datetime.timedelta(minutes=5)},
        duar_priv, algorithm="RS256",
    )
    return idp_token, authz_token


def _app(keypairs, *, effective_scope: str) -> Starlette:
    async def protected(request: Request) -> JSONResponse:
        return JSONResponse({"role": request.state.user.workspace_role})

    app = Starlette(routes=[Route("/protected", protected)])
    app.add_middleware(
        AuthzMiddleware,
        service_name="docs",  # the member's own name — NOT the realm slug
        idp_audience="client-id",
        idp_public_key=keypairs["idp"][1],
        duar_public_key=keypairs["duar"][1],
        duar_instance=_FakeInstance(effective_scope),
    )
    return app


def test_member_accepts_token_minted_for_realm_slug(keypairs):
    idp, authz = _tokens(keypairs, svc="acme-suite")  # realm-scoped token
    client = TestClient(_app(keypairs, effective_scope="acme-suite"))
    resp = client.get("/protected", headers={"Authorization": f"Bearer {idp}", "X-Authz-Token": authz})
    assert resp.status_code == 200
    assert resp.json()["role"] == "editor"


def test_member_rejects_token_minted_for_bare_service_name(keypairs):
    # A token whose svc is the old bare name must NOT validate once the service
    # is a realm member (effective_scope is the slug).
    idp, authz = _tokens(keypairs, svc="docs")
    client = TestClient(_app(keypairs, effective_scope="acme-suite"))
    resp = client.get("/protected", headers={"Authorization": f"Bearer {idp}", "X-Authz-Token": authz})
    assert resp.status_code == 403


def test_standalone_unchanged_accepts_service_name(keypairs):
    # effective_scope == service_name (standalone): today's behavior, unchanged.
    idp, authz = _tokens(keypairs, svc="docs")
    client = TestClient(_app(keypairs, effective_scope="docs"))
    resp = client.get("/protected", headers={"Authorization": f"Bearer {idp}", "X-Authz-Token": authz})
    assert resp.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sdk && uv run pytest tests/test_authz_effective_scope.py -v`
Expected: FAIL — `test_member_accepts_token_minted_for_realm_slug` gets 403 (the check still compares against `service_name="docs"`), `test_member_rejects_token_minted_for_bare_service_name` gets 200.

- [ ] **Step 3: Add the `effective_scope` property to `AuthzMiddleware`**

In `sdk/src/duar_auth/authz_middleware.py`, add after the `duar_public_key` property (around line 131):

```python
    @property
    def effective_scope(self) -> str:
        """The shared scope an incoming authz token's ``svc`` must match.

        A realm member resolves this from its Duar instance (discovered via
        ``whoami`` at startup); standalone services and static-key (air-gapped) mode
        fall back to ``service_name`` — today's behavior, unchanged.
        """
        if self._duar_instance is not None:
            return self._duar_instance.effective_scope
        return self.service_name
```

- [ ] **Step 4: Broaden the step-6 check**

In `authz_middleware.py`, in `dispatch`, change the svc-binding check (currently
comparing `token_svc != self.service_name`) to use `effective_scope`:

```python
        # 6. Enforce svc binding: the authz token was minted for this service's
        #    effective scope (the realm slug for a member, else the service name).
        token_svc = authz_payload.get("svc")
        if not token_svc or token_svc != self.effective_scope:
            return JSONResponse(
                status_code=403,
                content={"detail": "Authz token was issued for a different service"},
            )
```

- [ ] **Step 5: Run the new test + the existing middleware test (no regression)**

Run: `cd sdk && uv run pytest tests/test_authz_effective_scope.py tests/test_authz_middleware.py -v`
Expected: PASS. The existing `test_authz_middleware.py` uses static-key mode (no instance), so `effective_scope` falls back to `service_name` and its assertions are unchanged.

- [ ] **Step 6: Lint + commit**

```bash
cd sdk && uv run ruff format src/duar_auth/authz_middleware.py tests/test_authz_effective_scope.py && uv run ruff check --fix src/duar_auth/authz_middleware.py tests/test_authz_effective_scope.py
git add sdk/src/duar_auth/authz_middleware.py sdk/tests/test_authz_effective_scope.py
git commit -m "feat(sdk): broaden authz svc check to effective_scope (realm-shared tokens)"
```

---

### Task A4: `verify_m2m_token()` → `SystemAuth` + `require_system` dependency

**Files:**
- Modify: `sdk/src/duar_auth/duar.py`
- Test: `sdk/tests/test_m2m_verify.py`

**Interfaces:**
- Consumes: `SystemAuth` (Task A1), `effective_scope` (Task A2), `self.duar_public_key`, `jwt`, `DuarError`, FastAPI `Request`/`HTTPException`.
- Produces on `Duar`:
  - module constant `_AUD_M2M = "sentinel:m2m"`.
  - `verify_m2m_token(token: str) -> SystemAuth` — RS256-verify against the lifespan-fetched Duar public key, `aud=_AUD_M2M`; assert `type=="m2m"`, `svc==effective_scope`, optional `aud_target==service_name`; returns `SystemAuth(caller, actions, svc)`. Raises `DuarError` (with `status_code` 401/403) on failure.
  - `require_system` property → a FastAPI dependency returning `SystemAuth` from the `Authorization: Bearer` header.

- [ ] **Step 1: Write the failing test**

```python
# sdk/tests/test_m2m_verify.py
"""Duar.verify_m2m_token: accept a no-user realm token -> SystemAuth."""

import datetime
import uuid

import jwt as pyjwt
import pytest

from duar_auth import Duar, SystemAuth
from duar_auth.types import DuarError


def _duar(public_pem: str, *, effective_scope: str = "acme-suite", service_name: str = "reports") -> Duar:
    s = Duar(
        base_url="https://duar.test",
        service_name=service_name,
        service_key="svc-key",
        idp_public_key="-----BEGIN PUBLIC KEY-----\nx\n-----END PUBLIC KEY-----",
        idp_audience="my-client-id",
    )
    s._duar_public_key = public_pem  # normally set by lifespan
    s._effective_scope = effective_scope
    return s


def _m2m(private_pem: str, *, svc="acme-suite", caller="docs", actions=None,
         aud="sentinel:m2m", typ="m2m", aud_target=None, ttl=300) -> str:
    now = datetime.datetime.now(datetime.UTC)
    return pyjwt.encode(
        {"iss": "https://duar.test", "aud": aud, "type": typ, "svc": svc,
         "caller": caller, "actions": actions if actions is not None else ["*"],
         "aud_target": aud_target, "jti": str(uuid.uuid4()),
         "iat": now, "exp": now + datetime.timedelta(seconds=ttl)},
        private_pem, algorithm="RS256",
    )


def test_accepts_valid_m2m_token(rsa_keypair):
    private_pem, public_pem = rsa_keypair
    s = _duar(public_pem)
    sys_auth = s.verify_m2m_token(_m2m(private_pem))
    assert isinstance(sys_auth, SystemAuth)
    assert sys_auth.caller == "docs"
    assert sys_auth.svc == "acme-suite"
    assert sys_auth.can("anything") is True


def test_rejects_cross_realm_svc(rsa_keypair):
    private_pem, public_pem = rsa_keypair
    s = _duar(public_pem, effective_scope="acme-suite")
    with pytest.raises(DuarError) as exc:
        s.verify_m2m_token(_m2m(private_pem, svc="other-realm"))
    assert exc.value.status_code == 403


def test_rejects_wrong_audience(rsa_keypair):
    private_pem, public_pem = rsa_keypair
    s = _duar(public_pem)
    # A user authz token (aud=sentinel:authz) must never validate as m2m.
    with pytest.raises(DuarError):
        s.verify_m2m_token(_m2m(private_pem, aud="sentinel:authz", typ="authz"))


def test_rejects_expired(rsa_keypair):
    private_pem, public_pem = rsa_keypair
    s = _duar(public_pem)
    with pytest.raises(DuarError):
        s.verify_m2m_token(_m2m(private_pem, ttl=-10))


def test_aud_target_must_match_when_set(rsa_keypair):
    private_pem, public_pem = rsa_keypair
    s = _duar(public_pem, service_name="reports")
    # aud_target narrows the token to one service; reports != billing -> reject.
    with pytest.raises(DuarError) as exc:
        s.verify_m2m_token(_m2m(private_pem, aud_target="billing"))
    assert exc.value.status_code == 403
    # ...and is accepted when it matches this service.
    ok = s.verify_m2m_token(_m2m(private_pem, aud_target="reports"))
    assert ok.svc == "acme-suite"


def test_raises_when_public_key_missing(rsa_keypair):
    private_pem, _ = rsa_keypair
    s = Duar(base_url="https://duar.test", service_name="reports",
                 service_key="k", idp_public_key="x", idp_audience="a")
    with pytest.raises(DuarError):
        s.verify_m2m_token(_m2m(private_pem))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sdk && uv run pytest tests/test_m2m_verify.py -v`
Expected: FAIL — `AttributeError: 'Duar' object has no attribute 'verify_m2m_token'`.

- [ ] **Step 3: Add imports + the `_AUD_M2M` constant**

In `sdk/src/duar_auth/duar.py`, extend the imports:

```python
import jwt
```

(add near the top, with the other stdlib/third-party imports), and change the FastAPI import line to:

```python
from fastapi import FastAPI, HTTPException, Request
```

Change the auth import to also bring in `SystemAuth`:

```python
from duar_auth.auth import SystemAuth
```

(`RequestAuth` is not imported here today; only add `SystemAuth`.) Then add the
module-level constant just below the imports, before `class Duar`:

```python
_AUD_M2M = "sentinel:m2m"
```

Also ensure `DuarError` is importable — add:

```python
from duar_auth.types import DuarError
```

- [ ] **Step 4: Add `verify_m2m_token`**

In `duar.py`, add after the `require_action` method (end of the class):

```python
    # -- No-user (m2m) tokens -------------------------------------------------

    def verify_m2m_token(self, token: str) -> SystemAuth:
        """Verify an inbound no-user realm token and return its ``SystemAuth``.

        Receiver side of Flow B: App B calls this on a token App A minted via
        ``mint_m2m_token``. Trust is rooted entirely in Duar's RS256 signature
        (only Duar holds the private key) plus aud/type/svc binding — never
        app↔app trust. The token's ``svc`` must equal this service's
        ``effective_scope``, so a token minted for another realm cannot be replayed.

        Mount the protected route OUTSIDE ``AuthzMiddleware`` (add it to
        ``exclude_paths``): an m2m call carries no IdP token, so the dual-token
        middleware would 401 it. Gate it with ``require_system`` instead.

        Raises ``DuarError`` (``status_code`` 401 for bad/expired/wrong-type,
        403 for wrong realm / wrong target).
        """
        key = self._duar_public_key
        if not key:
            raise DuarError(
                "Duar public key not available; run the app under "
                "duar.lifespan so it is fetched at startup.",
                503,
            )
        try:
            # ponytail: verifies against the single PEM fetched once at lifespan —
            # not kid-rotation-aware mid-process like AuthzMiddleware's PyJWKClient.
            # m2m TTL is short (default 300s) and a restart refetches; upgrade to a
            # PyJWKClient against {base_url}/.well-known/jwks.json if mid-process key
            # rotation must not interrupt m2m acceptance.
            payload = jwt.decode(token, key, algorithms=["RS256"], audience=_AUD_M2M)
        except jwt.ExpiredSignatureError as exc:
            raise DuarError("m2m token expired", 401) from exc
        except jwt.InvalidTokenError as exc:
            raise DuarError("Invalid m2m token", 401) from exc
        if payload.get("type") != "m2m":
            raise DuarError("Not an m2m token", 401)
        if payload.get("svc") != self.effective_scope:
            raise DuarError("m2m token was issued for a different realm", 403)
        aud_target = payload.get("aud_target")
        if aud_target is not None and aud_target != self.service_name:
            raise DuarError("m2m token targets a different service", 403)
        return SystemAuth(
            caller=payload.get("caller", ""),
            actions=list(payload.get("actions") or []),
            svc=payload["svc"],
        )

    @property
    def require_system(self) -> Callable:
        """FastAPI dependency returning a ``SystemAuth`` for a no-user m2m call.

        Reads the m2m token from ``Authorization: Bearer`` (its only credential —
        there is no user). Raise this route's path in the middleware ``exclude_paths``.
        """

        def dependency(request: Request) -> SystemAuth:
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                raise HTTPException(status_code=401, detail="Missing m2m token")
            try:
                return self.verify_m2m_token(auth.removeprefix("Bearer "))
            except DuarError as exc:
                raise HTTPException(status_code=exc.status_code or 401, detail=str(exc))

        return dependency
```

- [ ] **Step 4b: Run test to verify it passes**

Run: `cd sdk && uv run pytest tests/test_m2m_verify.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
cd sdk && uv run ruff format src/duar_auth/duar.py tests/test_m2m_verify.py && uv run ruff check --fix src/duar_auth/duar.py tests/test_m2m_verify.py
git add sdk/src/duar_auth/duar.py sdk/tests/test_m2m_verify.py
git commit -m "feat(sdk): verify_m2m_token + require_system (accept no-user realm tokens)"
```

---

### Task A5: `mint_m2m_token()` with ~80%-TTL auto-refresh

**Files:**
- Modify: `sdk/src/duar_auth/duar.py`
- Test: `sdk/tests/test_mint_m2m.py`

**Interfaces:**
- Consumes: `httpx`, `time`, `DuarError`, `self.base_url`, `self.service_key`.
- Produces on `Duar`: `async mint_m2m_token() -> str` — returns a cached token while within ~80% of its TTL, else POSTs `/realm/m2m-token` (`X-Service-Key`, empty body), caches `{token, expires_in}`, and returns the fresh token. Adds `_m2m_token`/`_m2m_refresh_at` state.

- [ ] **Step 1: Write the failing test**

```python
# sdk/tests/test_mint_m2m.py
"""Duar.mint_m2m_token: mints, caches within TTL, re-mints after ~80%."""

import httpx
import pytest
import respx

from duar_auth import Duar
from duar_auth.types import DuarError


def _duar() -> Duar:
    return Duar(
        base_url="https://duar.test",
        service_name="docs",
        service_key="svc-key",
        idp_public_key="x",
        idp_audience="a",
    )


@respx.mock
async def test_mints_then_serves_from_cache():
    route = respx.post("https://duar.test/realm/m2m-token").mock(
        return_value=httpx.Response(200, json={"token": "tok-1", "expires_in": 300})
    )
    s = _duar()
    assert await s.mint_m2m_token() == "tok-1"
    # Second call is within the 80%-TTL window: cached, no second HTTP call.
    assert await s.mint_m2m_token() == "tok-1"
    assert route.call_count == 1
    # X-Service-Key was sent.
    assert route.calls[0].request.headers["X-Service-Key"] == "svc-key"


@respx.mock
async def test_remints_after_refresh_window():
    respx.post("https://duar.test/realm/m2m-token").mock(
        return_value=httpx.Response(200, json={"token": "tok-1", "expires_in": 300})
    )
    s = _duar()
    await s.mint_m2m_token()
    # Force the refresh deadline into the past → next call re-mints.
    s._m2m_refresh_at = 0.0
    respx.post("https://duar.test/realm/m2m-token").mock(
        return_value=httpx.Response(200, json={"token": "tok-2", "expires_in": 300})
    )
    assert await s.mint_m2m_token() == "tok-2"


@respx.mock
async def test_mint_rejection_raises():
    respx.post("https://duar.test/realm/m2m-token").mock(
        return_value=httpx.Response(403, json={"detail": "not a realm member"})
    )
    s = _duar()
    with pytest.raises(DuarError) as exc:
        await s.mint_m2m_token()
    assert exc.value.status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sdk && uv run pytest tests/test_mint_m2m.py -v`
Expected: FAIL — `AttributeError: 'Duar' object has no attribute 'mint_m2m_token'`.

- [ ] **Step 3: Add `import time` + cache state**

In `duar.py`, add `import time` with the other stdlib imports. In `__init__`,
after the `self._realm` line added in Task A2, add:

```python
        self._m2m_token: str | None = None
        self._m2m_refresh_at: float = 0.0
```

- [ ] **Step 4: Add `mint_m2m_token`**

In `duar.py`, add after `verify_m2m_token` (in the m2m section):

```python
    async def mint_m2m_token(self) -> str:
        """Mint (or return a cached) no-user realm m2m token for an outbound call.

        Sender side of Flow B: App A calls this, then forwards the token in
        ``Authorization: Bearer`` on its call to App B. The token is cached and only
        re-minted once it passes ~80% of its TTL, so a tight background loop doesn't
        hammer Duar. Requires this service to be an active member of an active
        realm (Duar rejects a standalone caller with 403).
        """
        if self._m2m_token is not None and time.monotonic() < self._m2m_refresh_at:
            return self._m2m_token
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self.base_url}/realm/m2m-token",
                json={},
                headers={"X-Service-Key": self.service_key},
            )
        if resp.status_code != 200:
            raise DuarError(f"m2m mint failed: {resp.status_code}", resp.status_code)
        data = resp.json()
        self._m2m_token = data["token"]
        self._m2m_refresh_at = time.monotonic() + data["expires_in"] * 0.8
        return self._m2m_token
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd sdk && uv run pytest tests/test_mint_m2m.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Run the full Python SDK suite (no regression)**

Run: `cd sdk && uv run pytest -q`
Expected: PASS (all green — this package's tests are pure-unit/respx with no network).

- [ ] **Step 7: Commit**

```bash
cd sdk && uv run ruff format src/duar_auth/duar.py tests/test_mint_m2m.py && uv run ruff check --fix src/duar_auth/duar.py tests/test_mint_m2m.py
git add sdk/src/duar_auth/duar.py sdk/tests/test_mint_m2m.py
git commit -m "feat(sdk): mint_m2m_token with ~80%-TTL auto-refresh"
```

---

## Part B — JS SDKs (`sdks/js`, `sdks/nextjs`)

### Task B1: m2m/whoami types + `verifyM2mToken` (accept) in `@duar-auth/js/server`

**Files:**
- Modify: `sdks/js/src/types.ts` (add m2m/whoami types)
- Create: `sdks/js/src/m2m.ts` (`verifyM2mToken`, `fetchWhoami`)
- Modify: `sdks/js/src/server.ts` (export them)
- Test: `sdks/js/src/__tests__/m2m.test.ts`

**Interfaces:**
- Consumes: `jose` `jwtVerify` + the module-scoped JWKS cache (re-implemented locally, mirroring `jwt-verifier.ts`).
- Produces:
  - Types: `M2mJWTPayload`, `WhoamiResponse`, `M2mVerifyOptions`, `SystemAuth`.
  - `verifyM2mToken(token, options) -> Promise<SystemAuth>` — verifies `aud=sentinel:m2m`, `type=m2m`, `svc===options.effectiveScope`, optional `aud_target===options.serviceName`; returns `{ caller, actions, svc, can(action) }`.
  - `fetchWhoami({ duarUrl, serviceKey }) -> Promise<WhoamiResponse>`.

- [ ] **Step 1: Write the failing test**

```typescript
// sdks/js/src/__tests__/m2m.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('jose', () => ({
  createRemoteJWKSet: vi.fn(() => vi.fn()),
  jwtVerify: vi.fn(),
}))

import { verifyM2mToken, fetchWhoami } from '../m2m'
import { jwtVerify } from 'jose'

const JWKS = 'http://localhost:9010/.well-known/jwks.json'

function mockM2mPayload(over: Record<string, unknown> = {}) {
  vi.mocked(jwtVerify).mockResolvedValue({
    payload: {
      iss: 'http://localhost:9010',
      aud: 'sentinel:m2m',
      type: 'm2m',
      svc: 'acme-suite',
      caller: 'docs',
      actions: ['*'],
      aud_target: null,
      exp: Math.floor(Date.now() / 1000) + 300,
      iat: Math.floor(Date.now() / 1000),
      ...over,
    } as any,
    protectedHeader: { alg: 'RS256' },
    key: {} as any,
  } as any)
}

describe('verifyM2mToken', () => {
  beforeEach(() => vi.clearAllMocks())

  it('returns a SystemAuth for a valid token', async () => {
    mockM2mPayload()
    const sys = await verifyM2mToken('tok', { jwksUrl: JWKS, effectiveScope: 'acme-suite' })
    expect(sys.caller).toBe('docs')
    expect(sys.svc).toBe('acme-suite')
    expect(sys.can('anything')).toBe(true)
  })

  it('verifies against the sentinel:m2m audience', async () => {
    mockM2mPayload()
    await verifyM2mToken('tok', { jwksUrl: JWKS, effectiveScope: 'acme-suite' })
    expect(jwtVerify).toHaveBeenCalledWith(
      'tok', expect.anything(), expect.objectContaining({ audience: 'sentinel:m2m' }),
    )
  })

  it('rejects a token minted for another realm', async () => {
    mockM2mPayload({ svc: 'other-realm' })
    await expect(
      verifyM2mToken('tok', { jwksUrl: JWKS, effectiveScope: 'acme-suite' }),
    ).rejects.toThrow(/realm/i)
  })

  it('rejects a non-m2m token type', async () => {
    mockM2mPayload({ type: 'authz' })
    await expect(
      verifyM2mToken('tok', { jwksUrl: JWKS, effectiveScope: 'acme-suite' }),
    ).rejects.toThrow(/m2m/i)
  })

  it('honors aud_target when set', async () => {
    mockM2mPayload({ aud_target: 'billing' })
    await expect(
      verifyM2mToken('tok', { jwksUrl: JWKS, effectiveScope: 'acme-suite', serviceName: 'reports' }),
    ).rejects.toThrow(/target/i)
  })

  it('narrows actions: specific action allowed/denied', async () => {
    mockM2mPayload({ actions: ['reports:read'] })
    const sys = await verifyM2mToken('tok', { jwksUrl: JWKS, effectiveScope: 'acme-suite' })
    expect(sys.can('reports:read')).toBe(true)
    expect(sys.can('reports:write')).toBe(false)
  })
})

describe('fetchWhoami', () => {
  beforeEach(() => vi.clearAllMocks())

  it('GETs /realm/whoami with the service key', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(
      new Response(JSON.stringify({ service_name: 'docs', effective_scope: 'acme-suite', realm: { slug: 'acme-suite', name: 'Acme' } }), { status: 200 }),
    ))
    const who = await fetchWhoami({ duarUrl: 'http://localhost:9010', serviceKey: 'k' })
    expect(who.effective_scope).toBe('acme-suite')
    const [url, init] = vi.mocked(fetch).mock.calls[0]
    expect(url).toBe('http://localhost:9010/realm/whoami')
    expect((init?.headers as Record<string, string>)['X-Service-Key']).toBe('k')
    vi.restoreAllMocks()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sdks/js && npm test -- m2m`
Expected: FAIL — cannot resolve `../m2m`.

- [ ] **Step 3: Add the types**

Append to `sdks/js/src/types.ts`:

```typescript
// ── Realm m2m (no-user) ─────────────────────────────────────────────

export interface M2mJWTPayload {
  svc: string
  caller: string
  actions: string[]
  aud_target: string | null
  aud: string | string[]
  iss: string
  exp: number
  iat: number
  jti: string
  type: 'm2m'
}

export interface WhoamiResponse {
  service_name: string
  effective_scope: string
  realm: { slug: string; name: string } | null
}

export interface M2mVerifyOptions {
  /** JWKS URL of the Duar that signs m2m tokens. */
  jwksUrl: string
  /** This service's shared scope (realm slug). The token's `svc` must equal it. */
  effectiveScope: string
  /** This service's own name — checked against `aud_target` when the token sets it. */
  serviceName?: string
  /** Expected issuer claim. */
  issuer?: string
}

/** No-user in-realm caller context — the counterpart to a DuarUser. */
export interface SystemAuth {
  caller: string
  actions: string[]
  svc: string
  can(action: string): boolean
}
```

- [ ] **Step 4: Create `m2m.ts`**

```typescript
// sdks/js/src/m2m.ts
import { createRemoteJWKSet, jwtVerify } from 'jose'
import type { M2mJWTPayload, M2mVerifyOptions, SystemAuth, WhoamiResponse } from './types'

const M2M_AUDIENCE = 'sentinel:m2m'

const jwksSets = new Map<string, ReturnType<typeof createRemoteJWKSet>>()

function getJWKS(url: string) {
  let jwks = jwksSets.get(url)
  if (!jwks) {
    jwks = createRemoteJWKSet(new URL(url))
    jwksSets.set(url, jwks)
  }
  return jwks
}

/**
 * Verify an inbound no-user realm token (server entry only — never the browser).
 *
 * Receiver side of Flow B. Trust is rooted in Duar's RS256 signature plus
 * aud/type/svc binding — never app↔app trust. The token's `svc` must equal this
 * service's `effectiveScope`, so a token minted for another realm cannot be
 * replayed here. Throws on any failure.
 */
export async function verifyM2mToken(
  token: string,
  options: M2mVerifyOptions,
): Promise<SystemAuth> {
  const { payload } = await jwtVerify(token, getJWKS(options.jwksUrl), {
    audience: M2M_AUDIENCE,
    issuer: options.issuer,
  })
  const claims = payload as unknown as M2mJWTPayload
  if (claims.type !== 'm2m') {
    throw new Error('Not an m2m token')
  }
  if (claims.svc !== options.effectiveScope) {
    throw new Error('m2m token was issued for a different realm')
  }
  if (
    claims.aud_target != null &&
    options.serviceName !== undefined &&
    claims.aud_target !== options.serviceName
  ) {
    throw new Error('m2m token targets a different service')
  }
  const actions = claims.actions ?? []
  return {
    caller: claims.caller,
    actions,
    svc: claims.svc,
    can: (action: string) => actions.includes('*') || actions.includes(action),
  }
}

/**
 * Self-discover this service's shared scope from Duar (server entry only).
 * Standalone services get `effective_scope === service_name` and `realm: null`.
 */
export async function fetchWhoami(opts: {
  duarUrl: string
  serviceKey: string
}): Promise<WhoamiResponse> {
  const base = opts.duarUrl.replace(/\/+$/, '')
  const res = await fetch(`${base}/realm/whoami`, {
    headers: { 'X-Service-Key': opts.serviceKey },
  })
  if (!res.ok) throw new Error(`whoami failed: ${res.status}`)
  return res.json() as Promise<WhoamiResponse>
}
```

- [ ] **Step 5: Export from the server entry**

In `sdks/js/src/server.ts`, add:

```typescript
export { verifyM2mToken, fetchWhoami } from './m2m'
```

and extend the `export type { ... } from './types'` block with `M2mJWTPayload`,
`WhoamiResponse`, `M2mVerifyOptions`, `SystemAuth`.

- [ ] **Step 6: Run test + build**

Run: `cd sdks/js && npm test -- m2m && npm run build`
Expected: tests PASS; build succeeds (types resolve).

- [ ] **Step 7: Commit**

```bash
git add sdks/js/src/types.ts sdks/js/src/m2m.ts sdks/js/src/server.ts sdks/js/src/__tests__/m2m.test.ts
git commit -m "feat(js-sdk): verifyM2mToken + fetchWhoami in server entry"
```

---

### Task B2: `M2mTokenClient` mint + ~80%-TTL refresh in `@duar-auth/js/server`

**Files:**
- Modify: `sdks/js/src/m2m.ts` (add `M2mTokenClient`)
- Modify: `sdks/js/src/server.ts` (export it)
- Test: extend `sdks/js/src/__tests__/m2m.test.ts`

**Interfaces:**
- Produces: `class M2mTokenClient { constructor(duarUrl: string, serviceKey: string); getToken(): Promise<string> }` — caches the minted token and only re-mints once past ~80% of `expires_in`. Server entry only.

- [ ] **Step 1: Add the failing test (append to `m2m.test.ts`)**

```typescript
import { M2mTokenClient } from '../m2m'

describe('M2mTokenClient', () => {
  beforeEach(() => vi.clearAllMocks())

  it('mints then serves from cache within the TTL window', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ token: 'tok-1', expires_in: 300 }), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const c = new M2mTokenClient('http://localhost:9010', 'k')
    expect(await c.getToken()).toBe('tok-1')
    expect(await c.getToken()).toBe('tok-1')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('http://localhost:9010/realm/m2m-token')
    expect(init.method).toBe('POST')
    expect((init.headers as Record<string, string>)['X-Service-Key']).toBe('k')
    vi.restoreAllMocks()
  })

  it('throws when Duar rejects the mint', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: 'not a realm member' }), { status: 403 }),
    ))
    const c = new M2mTokenClient('http://localhost:9010', 'k')
    await expect(c.getToken()).rejects.toThrow(/403/)
    vi.restoreAllMocks()
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd sdks/js && npm test -- m2m`
Expected: FAIL — `M2mTokenClient` is not exported.

- [ ] **Step 3: Add `M2mTokenClient` to `m2m.ts`**

Append to `sdks/js/src/m2m.ts`:

```typescript
/**
 * Mints and caches no-user realm m2m tokens for outbound system calls (sender
 * side of Flow B). Server entry only — never construct this in a browser; it
 * holds the service key. Re-mints only once past ~80% of the token's TTL.
 */
export class M2mTokenClient {
  private readonly base: string
  private readonly serviceKey: string
  private token: string | null = null
  private refreshAt = 0

  constructor(duarUrl: string, serviceKey: string) {
    this.base = duarUrl.replace(/\/+$/, '')
    this.serviceKey = serviceKey
  }

  /** Return a cached token if still fresh, else mint a new one. */
  async getToken(): Promise<string> {
    if (this.token && Date.now() < this.refreshAt) return this.token
    const res = await fetch(`${this.base}/realm/m2m-token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Service-Key': this.serviceKey },
      body: JSON.stringify({}),
    })
    if (!res.ok) throw new Error(`m2m mint failed: ${res.status}`)
    const data = (await res.json()) as { token: string; expires_in: number }
    this.token = data.token
    this.refreshAt = Date.now() + data.expires_in * 0.8 * 1000
    return this.token
  }
}
```

- [ ] **Step 4: Export it**

In `sdks/js/src/server.ts`, change the m2m export to:

```typescript
export { verifyM2mToken, fetchWhoami, M2mTokenClient } from './m2m'
```

- [ ] **Step 5: Run test + build + full js suite**

Run: `cd sdks/js && npm test && npm run build`
Expected: all PASS; build clean.

- [ ] **Step 6: Commit**

```bash
git add sdks/js/src/m2m.ts sdks/js/src/server.ts sdks/js/src/__tests__/m2m.test.ts
git commit -m "feat(js-sdk): M2mTokenClient mint+refresh in server entry"
```

---

### Task B3: Broaden the Next.js authz-middleware `svc` check to scope

**Files:**
- Modify: `sdks/nextjs/src/authz-middleware.ts`

**Interfaces:**
- Adds an optional `effectiveScope?: string` to `DuarAuthzMiddlewareConfig`. The svc-binding check accepts a token whose `svc` equals **either** `serviceName` (standalone — unchanged) **or** `effectiveScope` (realm slug). A realm member sets `effectiveScope` to its realm slug (resolved once at startup via `fetchWhoami`, or from an env var); a standalone app omits it and behaves exactly as today.

> **Why config, not auto-whoami:** the middleware runs on the Edge per-request; an async whoami call there adds cold-start latency and needs a service key in the edge bundle. Passing the already-known slug in is the lazy, non-magic choice. `fetchWhoami` (Task B1) is available for apps that want to resolve it at startup.

- [ ] **Step 1: Add `effectiveScope` to the config interface**

In `sdks/nextjs/src/authz-middleware.ts`, add to `DuarAuthzMiddlewareConfig`
(after the `serviceName` field):

```typescript
  /**
   * Realm slug (this service's shared scope). When set, the authz token's `svc`
   * may equal either `serviceName` or this. Realm members resolve it once at
   * startup via `fetchWhoami` from `@duar-auth/js/server`. Omit for standalone.
   */
  effectiveScope?: string
```

- [ ] **Step 2: Destructure it and broaden the check**

In `createDuarAuthzMiddleware`, add `effectiveScope` to the destructured
`config` (alongside `serviceName`):

```typescript
  const {
    duarUrl,
    idpJwksUrl,
    idpAudience,
    idpIssuer,
    serviceName,
    effectiveScope,
    publicPaths = [],
    loginPath = '/login',
  } = config
```

Then replace the step-4 svc check:

```typescript
      // Enforce svc binding: the authz token was minted for this service's shared
      // scope — its own name (standalone) or its realm slug (effectiveScope).
      const allowedSvc = new Set([serviceName, effectiveScope].filter(Boolean))
      if (!authzClaims.svc || !allowedSvc.has(authzClaims.svc as string)) {
        return handleUnauthenticated(req, loginPath)
      }
```

- [ ] **Step 3: Build (typecheck) — nextjs has no unit harness**

Run: `cd sdks/nextjs && npm run build`
Expected: build succeeds (tsup emits `authz-middleware` with no type errors).

- [ ] **Step 4: Commit**

```bash
git add sdks/nextjs/src/authz-middleware.ts
git commit -m "feat(nextjs-sdk): broaden authz svc check to realm effectiveScope"
```

---

### Task B4: Re-export m2m from `@duar-auth/nextjs/server`

**Files:**
- Modify: `sdks/nextjs/src/server.ts`

**Interfaces:**
- Re-exports `verifyM2mToken`, `fetchWhoami`, `M2mTokenClient` (and the `SystemAuth`/`WhoamiResponse`/`M2mVerifyOptions` types) from `@duar-auth/js/server` so Next.js apps can mint/accept m2m in Route Handlers without a second import path. These are server-only — `nextjs/src/server.ts` is already the server entry (`getUser`, `requireUser`, etc.).

- [ ] **Step 1: Add the re-exports**

At the bottom of `sdks/nextjs/src/server.ts`:

```typescript
// Realm no-user (m2m) — server only. Mint for outbound system calls, verify inbound.
export { verifyM2mToken, fetchWhoami, M2mTokenClient } from '@duar-auth/js/server'
export type { SystemAuth, WhoamiResponse, M2mVerifyOptions } from '@duar-auth/js/server'
```
(The m2m types live in the `@duar-auth/js` **server** subpath — Task B1 adds them to `server.ts`, not the browser `index.ts` — so re-export them from `/server`, not the package root.)

- [ ] **Step 2: Build (typecheck)**

Run: `cd sdks/nextjs && npm run build`
Expected: build succeeds.

> If the build fails because `@duar-auth/js` is an unbuilt workspace dependency,
> run `cd sdks/js && npm run build` first (its `dist/` must exist for nextjs to
> resolve the types), then retry. Do **not** add new dependencies.

- [ ] **Step 3: Commit**

```bash
git add sdks/nextjs/src/server.ts
git commit -m "feat(nextjs-sdk): re-export m2m helpers from server entry"
```

---

## Self-review (done by plan author)

**Spec coverage (Plan 5 slice — SDK surface, spec lines 277–285):**
- Python `whoami` scope discovery (cached) → Task A2 (`fetch_whoami`, called once at lifespan; `effective_scope`/`realm` properties cache it).
- Python broaden authz `svc` check to `effective_scope` → Task A3.
- **Python `PermissionClient`/`RoleClient` send `effective_scope`** (the "transparent substitution", spec line 148; required — server `verify_service_scope` 403s otherwise; user-confirmed in scope) → Task A2 (lazy clients built with `effective_scope`; existing instances mutated in place after whoami).
- Python `mint_m2m_token()` with ~80%-TTL auto-refresh → Task A5.
- Python accept `type=m2m` → new `SystemAuth` (no user; `caller`, `actions`) alongside `RequestAuth` → Tasks A1 (type) + A4 (`verify_m2m_token`, `require_system`).
- JS user-context `svc` check broadened to scope (via whoami) → Task B3 (nextjs middleware `effectiveScope`) + Task B1 (`fetchWhoami` helper to resolve it).
- JS m2m **mint + accept** in the **server entry only**, never browser → Tasks B1 (`verifyM2mToken`), B2 (`M2mTokenClient`), B4 (nextjs server re-export). React untouched (browser-only, no server entry).

**Deferred (out of Plan 5):** docs (guide/api/sdk) + cross-SDK integration tests (Plan 6); per-member least-privilege `actions` enforcement, `aud_target` mint-side narrowing, `jti` denylist (spec "Future work"). PyJWKClient-based kid-rotation for m2m verify (marked `# ponytail:` in Task A4 with the upgrade path).

**Placeholder scan:** none — every code/command step carries full content.

**Type consistency:**
- `SystemAuth(caller, actions, svc)` + `can(action)` — defined A1, returned by A4's `verify_m2m_token`; the JS `SystemAuth` interface (B1) mirrors it (`caller`, `actions`, `svc`, `can`).
- `_AUD_M2M = "sentinel:m2m"` (A4) matches the server's `_AUD_M2M` (Plan 2) and the JS `M2M_AUDIENCE` (B1).
- `effective_scope` property (A2) consumed by A3 (`AuthzMiddleware.effective_scope`), A4 (`verify_m2m_token` svc check), and the A2 client substitution — one source of truth.
- `fetch_whoami` / `WhoamiResponse` shape `{service_name, effective_scope, realm}` matches the server `/realm/whoami` (Plan 2) on both Python (A2) and JS (B1) sides.
- `mint_m2m_token` (A5) / `M2mTokenClient.getToken` (B2) both POST `/realm/m2m-token` with `{}` body + `X-Service-Key`, parse `{token, expires_in}`, refresh at 80% — matching the server `M2MTokenResponse` (Plan 2).

**Reused-not-added:** Python reuses the existing `httpx` client style, `_TTLCache` is *not* needed (token caching is a single value + deadline), the conftest `rsa_keypair`/`make_token` fixtures, and the lifespan seam. JS reuses the `jose` JWKS-cache pattern from `jwt-verifier.ts` and the existing vitest `fetch`/`jose` mocking style. No new dependencies in either tree.

**Known integration gaps (call out at execution):** the `require_system`-gated route must be in the `AuthzMiddleware`/nextjs-middleware `exclude_paths`/`publicPaths` (m2m calls carry no IdP token) — documented in A4's docstring; real JWKS/key-rotation paths and the live `/realm` endpoints are exercised only at runtime (`make start` + the internal listener), not by these pure-unit/mocked suites. End-to-end Flow A/B across two real apps lands with Plan 6's integration tests.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-26-realm-sdk-m2m.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task (A1→A5, then B1→B4), two-stage review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session with checkpoints.
