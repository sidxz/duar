# Realm Network Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split Duar into two listeners built from one image — a published **public** app (humans/browsers) and an **unpublished internal** app (the whole service-key surface: realm, permissions, authz, roles) — via a `create_app(tier)` factory, so the service-to-service endpoints have no socket on the public internet.

**Architecture:** A `create_app(tier)` factory in `service/src/main.py` builds the app, mounting tier-specific routers and middleware. `tier ∈ {public, internal, all}`: **public** = browser-facing routers + Session/CORS middleware; **internal** = service-key routers, no Session/CORS, no DB migration; **all** = both (today's single-process behavior — the default, so dev/`make start`/tests are unchanged). `TIER` env selects the tier. This task also **mounts the `/realm` router** (deferred from Plan 2) on the internal tier. A second task splits `docker-compose.prod.yml` into two services, the internal one unpublished.

**Tech Stack:** FastAPI, Starlette middleware, slowapi, uvicorn, Docker Compose / Swarm, pytest (managed by `uv`).

## Scope boundary (this is Plan 3 of 6)

1. Realm scope core — DONE. 2. Token flows — DONE (`/realm/whoami`, `/realm/m2m-token`, `create_m2m_token`; router **not yet mounted** — this plan mounts it). 3. **Network split** ← this plan. 4. Admin — `/admin/realms` CRUD + React (no `main.py` change). 5. SDKs. 6. Docs (incl. the internal-listener deployment posture).

After this plan: `create_app("internal")` serves only the service-key routers (incl. `/realm`), `create_app("public")` only the browser routers, `create_app("all")` both; prod compose runs both listeners with `:9010` unpublished. Verified by unit tests on router/middleware membership + a compose-validity check.

## Global Constraints

- Python 3.12; run everything via `uv` (`cd service && uv run ...`).
- Tests use **pure unit style** — `create_app(tier)` is inspected at **construction** time (`app.routes`, `app.user_middleware`); constructing an app does NOT run its lifespan, so no DB/Redis is touched. No `conftest.py`; import from `src.*` directly. Mark async tests `@pytest.mark.asyncio` (none needed here).
- Lint/format with ruff, **changed files only**: `cd service && uv run ruff format <changed files> && uv run ruff check --fix <changed files>`. NEVER `ruff format .` / whole-tree `--fix` / `make fmt`.
- **Stage only the files each task lists** (`git add <those paths>`); never `git add -A` / `git add .`.
- **Never modify** `service/src/services/role_service.py` or `service/tests/test_register_actions.py` — the user's separate uncommitted upsert work. (The earlier uncommitted rate-limit edits to `main.py`/`config.py` are now COMMITTED, so `main.py` is clean and free to refactor here.)
- Test gate = the task's **own** test file. The broad suite may show IdP/JWKS **connection** failures under the network-restricted sandbox — environmental, not task failures.
- Branch: `realm-trusted-app-group` (already checked out). Commit after every task.
- **Non-breaking invariant — the most important rule of this plan:** `create_app("all")` is the default (`TIER` unset) and MUST reproduce today's app — every current router present, same middleware, same lifespan (migrations + CORS warm). `make start`, the dev `docker-compose.yml`, the demo, and the entire existing test suite (which imports `from src.main import app`) must behave exactly as before. The split is **opt-in** via `TIER=public`/`TIER=internal` in production.
- **Migration safety:** only the `public` and `all` tiers run `_run_migrations()`. Two instances running `alembic upgrade head` at once can race; the internal listener trusts public to have migrated (compose orders it after public's healthcheck).
- **Router tier map (the audit, locked in):**
  - **internal:** `realm_router` (`/realm`), `permission_router` (`/permissions`), `authz_router` (`/authz`), `role_router` (`/roles`) — the service-key-only surface.
  - **public:** `admin_router` (`/admin`), `org_admin_router` (`/admin`), `auth_router` (`/auth`), `user_router` (`/users`), `workspace_router` (`/workspaces`), `group_router` (`/workspaces/{id}/groups`), `client_log_router` (`/internal`). `user`/`workspace`/`group` default **public** (they have browser consumers + their own user-JWT auth, so this never drops below today's exposure). `/health` on every tier; `/.well-known/jwks.json` on public + all (public keys are meant to be public).

---

### Task 1: `create_app(tier)` factory + tier split (+ mount `/realm`)

**Files:**
- Modify: `service/src/main.py` (refactor the app-construction section `:185-259`; tier-aware lifespan; add `import os`, `realm_router` import)
- Test: `service/tests/test_app_tiers.py`

**Interfaces:**
- Consumes: every existing router import in `main.py`; `realm_router` (Plan 2, `service/src/api/realm_routes.py`).
- Produces:
  - `create_app(tier: str) -> FastAPI` — builds a tier-specific app.
  - `_resolve_tier() -> str` — reads `TIER` env (default `"all"`); raises `RuntimeError` on an unknown value.
  - `PUBLIC_ROUTERS`, `INTERNAL_ROUTERS` — module-level lists.
  - Module-level `app = create_app(_resolve_tier())` (so `src.main:app` works as today).

- [ ] **Step 1: Write the failing test**

```python
# service/tests/test_app_tiers.py
"""create_app(tier) mounts the right routers + middleware per listener tier.

Apps are inspected at CONSTRUCTION time — building a FastAPI app does not run its
lifespan, so no DB/Redis is touched. 'all' (the default) must carry every route so
dev/make-start/tests are unchanged; 'public' drops the service-key surface; 'internal'
drops the browser surface AND the Session/CORS middleware.
"""

import pytest
from starlette.middleware.sessions import SessionMiddleware

from src.main import INTERNAL_ROUTERS, PUBLIC_ROUTERS, _resolve_tier, create_app
from src.middleware.cors import DynamicCORSMiddleware


def _paths(app) -> set[str]:
    return {getattr(r, "path", "") for r in app.routes}


def _has_prefix(app, prefix: str) -> bool:
    return any(p == prefix or p.startswith(prefix + "/") for p in _paths(app))


def test_public_tier_has_browser_routes_not_service_key_surface():
    app = create_app("public")
    assert _has_prefix(app, "/auth")  # auth proxy — public
    assert _has_prefix(app, "/admin")  # admin — public
    assert _has_prefix(app, "/users")  # public
    # Service-key surface must be ABSENT from the public listener:
    assert not _has_prefix(app, "/authz")
    assert not _has_prefix(app, "/permissions")
    assert not _has_prefix(app, "/realm")
    assert not _has_prefix(app, "/roles")


def test_internal_tier_has_service_key_surface_not_browser_routes():
    app = create_app("internal")
    assert _has_prefix(app, "/authz")
    assert _has_prefix(app, "/permissions")
    assert _has_prefix(app, "/realm")  # Plan 2 router, mounted here
    assert _has_prefix(app, "/roles")
    # Browser surface must be ABSENT from the internal listener:
    assert not _has_prefix(app, "/auth")  # note: /authz is present, /auth is not
    assert not _has_prefix(app, "/admin")
    assert not _has_prefix(app, "/users")
    assert not _has_prefix(app, "/workspaces")


def test_all_tier_is_todays_app_superset():
    app = create_app("all")
    for prefix in ("/auth", "/admin", "/users", "/workspaces", "/authz",
                   "/permissions", "/realm", "/roles"):
        assert _has_prefix(app, prefix), prefix


def test_health_on_every_tier():
    for tier in ("public", "internal", "all"):
        assert "/health" in _paths(create_app(tier))


def test_jwks_public_and_all_not_internal():
    assert "/.well-known/jwks.json" in _paths(create_app("public"))
    assert "/.well-known/jwks.json" in _paths(create_app("all"))
    assert "/.well-known/jwks.json" not in _paths(create_app("internal"))


def test_internal_tier_drops_session_and_cors_middleware():
    internal = {m.cls for m in create_app("internal").user_middleware}
    assert SessionMiddleware not in internal
    assert DynamicCORSMiddleware not in internal
    public = {m.cls for m in create_app("public").user_middleware}
    assert SessionMiddleware in public
    assert DynamicCORSMiddleware in public


def test_realm_router_is_internal_only():
    from src.api.realm_routes import router as realm_router

    assert realm_router in INTERNAL_ROUTERS
    assert realm_router not in PUBLIC_ROUTERS


def test_resolve_tier_default_is_all(monkeypatch):
    monkeypatch.delenv("TIER", raising=False)
    assert _resolve_tier() == "all"


def test_resolve_tier_rejects_unknown(monkeypatch):
    monkeypatch.setenv("TIER", "bogus")
    with pytest.raises(RuntimeError):
        _resolve_tier()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_app_tiers.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_app' from 'src.main'`.

- [ ] **Step 3: Add `import os` and the `realm_router` import**

In `service/src/main.py`, add `import os` as the first import (above `import time`). Then add the realm router import alongside the other router imports (right after the `authz_routes` import, currently line 13):

```python
from src.api.realm_routes import router as realm_router
```

- [ ] **Step 4: Make the lifespan tier-aware (via `app.state.tier`, no re-indentation)**

Keep the existing `lifespan` function's `@asynccontextmanager` decorator, signature, and structure **exactly as-is** — `create_app` will set `app.state.tier` before startup, and the lifespan reads it. Make exactly two edits inside the lifespan body:

(a) Read the tier at the top and log it. Replace (currently lines 58-59):

```python
    configure_logging()
    logger.info("app.startup", port=settings.service_port)
```

with:

```python
    configure_logging()
    tier = getattr(app.state, "tier", "all")
    logger.info("app.startup", port=settings.service_port, tier=tier)
```

(b) Guard the migration + CORS-warm block so only the migrator tiers run it. Replace (currently lines 60-69):

```python
    await _run_migrations()
    logger.info("app.db.migrated")

    # Warm CORS origin cache from active client apps
    from src.database import engine as db_engine

    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(db_engine) as db:
        await refresh_origins(db)
```

with:

```python
    # Only the migrator tiers touch the schema — two instances running
    # `alembic upgrade head` at once can race. public + all migrate; the internal
    # listener trusts public to have migrated (compose orders it after public's
    # healthcheck). CORS warm is likewise pointless where no CORS middleware mounts.
    if tier in ("public", "all"):
        await _run_migrations()
        logger.info("app.db.migrated")

        # Warm CORS origin cache from active client apps
        from src.database import engine as db_engine

        from sqlalchemy.ext.asyncio import AsyncSession

        async with AsyncSession(db_engine) as db:
            await refresh_origins(db)
```

Everything else in the lifespan (the Redis connectivity + security-config checks and `app.state.start_time`) stays exactly as-is — no other edits, no re-indentation.

- [ ] **Step 5: Replace the app-construction section with the factory**

In `service/src/main.py`, replace **everything from `app = FastAPI(` (currently line 185) through the end of the file** with:

```python
# Routers grouped by listener tier. public = browser/human surface; internal =
# the service-key-only surface (no socket on the public internet in a split deploy).
PUBLIC_ROUTERS = [
    admin_router,
    org_admin_router,
    auth_router,
    user_router,
    workspace_router,
    group_router,
    client_log_router,
]
INTERNAL_ROUTERS = [
    realm_router,
    permission_router,
    authz_router,
    role_router,
]


def _resolve_tier() -> str:
    """Which listener this process is: 'public', 'internal', or 'all' (default).

    'all' is a single combined app = today's behavior (dev, make start, tests, and
    small single-process deployments). Production opts into the split by setting
    TIER=public on the published service and TIER=internal on the unpublished one.
    """
    tier = os.getenv("TIER", "all").strip().lower()
    if tier not in ("all", "public", "internal"):
        raise RuntimeError(
            f"TIER must be one of all|public|internal, got {tier!r}"
        )
    return tier


def create_app(tier: str) -> FastAPI:
    """Build a listener for the given tier. Same image, different surface."""
    app = FastAPI(
        title=f"Duar ({tier})",
        description="Authentication, workspace management, and permissions",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        openapi_url="/openapi.json" if settings.debug else None,
    )
    # Stash the tier so the (shared, module-level) lifespan can gate migrations +
    # CORS warm. Set before startup runs, so the lifespan reads it via app.state.
    app.state.tier = tier

    # --- Middleware (last added = outermost, processes request first) ---

    # Reject oversized request bodies (10 MB)
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=10_485_760)

    # Rate limiting (slowapi). Deliberately positioned INSIDE
    # RequestContext/AccessLog/CORS (added after this) so a 429 still carries the
    # request-id, is access-logged, and gets CORS headers. See middleware/rate_limit.py.
    app.add_middleware(SlowAPIASGIMiddleware)

    # Security headers on every response
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.cookie_secure)

    # Session middleware is browser/OAuth-only — the internal listener has no human
    # callers, so it drops Session (and CORS below): less surface, and it cannot be
    # driven by a forged cookie/Origin.
    if tier in ("public", "all"):
        app.add_middleware(
            SessionMiddleware,
            secret_key=settings.session_secret_key,
            https_only=settings.cookie_secure,
            same_site="lax",
            max_age=600,  # 10 min — bounds the OAuth flow window
        )

    # Trusted host validation (prevents Host header attacks)
    if "*" not in settings.allowed_hosts_list:
        app.add_middleware(
            TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts_list
        )

    if tier in ("public", "all"):
        app.add_middleware(DynamicCORSMiddleware)
    app.add_middleware(AccessLogMiddleware)  # inside RequestContext
    app.add_middleware(RequestContextMiddleware)  # last added = outermost

    # Rate limiting state + handler
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    routers = []
    if tier in ("public", "all"):
        routers += PUBLIC_ROUTERS
    if tier in ("internal", "all"):
        routers += INTERNAL_ROUTERS
    for router in routers:
        app.include_router(router)

    @app.get("/health")
    @limiter.exempt  # health probes must never be throttled
    async def health():
        return {"status": "ok"}

    # JWKS is public by default — public keys are meant to be published. An
    # authz-only deployment (every verifier a backend) could move it internal.
    if tier in ("public", "all"):

        @app.get("/.well-known/jwks.json", tags=["auth"])
        async def jwks():
            from src.auth.jwks import build_jwks

            return build_jwks()

    return app


app = create_app(_resolve_tier())
```

- [ ] **Step 6: Run the tier test to verify it passes**

Run: `cd service && uv run pytest tests/test_app_tiers.py -v`
Expected: PASS (9 passed).

- [ ] **Step 7: Run the full suite (prove `all` == today's app — non-breaking)**

Run: `cd service && uv run pytest tests/ -q`
Expected: PASS. Existing tests import `from src.main import app` (now `create_app("all")`) — they must still pass. If the suite shows IdP/JWKS **connection** failures, those are the known network-sandbox artifact; confirm there are no NEW failures (especially nothing about a missing route or middleware) versus the Plan-2 baseline.

- [ ] **Step 8: Lint + commit**

```bash
cd service && uv run ruff format $FILES && uv run ruff check --fix $FILES   # $FILES = ONLY this task's files; never 'ruff format .' / 'make fmt'
git add service/src/main.py service/tests/test_app_tiers.py
git commit -m "feat(realm): create_app(tier) public/internal split + mount /realm internal"
```

---

### Task 2: Two-service production deployment (internal unpublished)

**Files:**
- Modify: `docker-compose.prod.yml` (anchor the shared `duar` env; add `TIER: public`; add a `duar-internal` service on `:9010`, unpublished)
- Test: `service/tests/test_compose_tiers.py` (validates the rendered compose: internal exists, `TIER: internal`, no published ports)

**Interfaces:**
- Consumes: the `TIER` env contract from Task 1 (`public`/`internal`); the image's default uvicorn CMD (overridden per service for the port).
- Produces: a prod stack with a published public listener (`:9003`) and an unpublished internal listener (`:9010`, overlay-only, reachable as `http://duar-internal:9010`).

- [ ] **Step 1: Write the failing test**

```python
# service/tests/test_compose_tiers.py
"""docker-compose.prod.yml runs two Duar listeners: a published public one and
an UNPUBLISHED internal one. This guards the deployment contract — the internal
service-key surface must never get a published port. PyYAML resolves the `<<` merge
key, so the merged `environment` (incl. the per-service TIER override) is asserted
on the loaded mapping without needing Docker."""

from pathlib import Path

import yaml

_COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.prod.yml"


def _services() -> dict:
    return yaml.safe_load(_COMPOSE.read_text())["services"]


def test_public_listener_is_published_with_tier_public():
    svc = _services()["duar"]
    assert svc["environment"]["TIER"] == "public"
    assert svc.get("ports"), "public listener must publish a port"


def test_internal_listener_exists_unpublished_with_tier_internal():
    services = _services()
    assert "duar-internal" in services, "internal listener service must exist"
    internal = services["duar-internal"]
    assert internal["environment"]["TIER"] == "internal"
    # The whole point of the split: the internal listener has NO socket on the host.
    assert not internal.get("ports"), "internal listener must NOT publish any port"


def test_internal_listener_waits_for_public_to_migrate():
    internal = _services()["duar-internal"]
    # public + all are the only migrator tiers; internal must start after public is
    # healthy so the schema exists before it serves authz/permissions.
    assert "duar" in internal.get("depends_on", {})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_compose_tiers.py -v`
Expected: FAIL — `KeyError: 'TIER'` (no `TIER` on `duar` yet) / `'duar-internal'` missing.

- [ ] **Step 3: Anchor the shared env + tag the public listener**

In `docker-compose.prod.yml`, in the `duar:` service, change the `environment:` line to anchor the block and add `TIER: public` as its first entry:

```yaml
    environment: &duar_env
      TIER: public
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}?ssl=require
```

(Leave every other line of that `environment:` mapping unchanged — only add the `&duar_env` anchor on the `environment:` key and the `TIER: public` line directly under it.)

- [ ] **Step 4: Add the unpublished internal listener**

In `docker-compose.prod.yml`, add this service immediately after the `duar:` service block (before `duar-admin:`). It reuses the same image + shared env (overriding `TIER`), overrides the uvicorn port to `9010`, and publishes **no** ports:

```yaml
  # Internal listener — the service-key-only surface (realm, permissions, authz,
  # roles). NOT published: reachable only on the `duar` overlay as
  # http://duar-internal:9010. Apps that hold a Duar service key point their
  # SDK base URL here. depends_on duar (public) so the schema is migrated first.
  duar-internal:
    image: ghcr.io/sidxz/duar:latest
    command: [".venv/bin/uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "9010", "--no-server-header"]
    environment:
      <<: *duar_env
      TIER: internal
    secrets:
      - jwt_private_key
      - jwt_public_key
      - tls_ca
    # NO `ports:` — unpublished by design; the public internet has no socket here.
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      duar:
        condition: service_healthy
    networks:
      - duar
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "wget", "-q", "--spider", "http://localhost:9010/health"]
      interval: 15s
      timeout: 5s
      retries: 3
      start_period: 10s
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: "1.0"
```

- [ ] **Step 5: Run the compose test to verify it passes**

Run: `cd service && uv run pytest tests/test_compose_tiers.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Validate the rendered compose (if Docker is available)**

Run: `docker compose -f docker-compose.prod.yml config >/dev/null && echo "compose valid"`
Expected: prints `compose valid`. Warnings about unset `${VARS}` are fine (they're supplied by `.env.prod` at deploy). If `docker` is not installed in this environment, skip — Step 5's YAML-level assertions are the gate.

- [ ] **Step 7: Commit**

```bash
git add docker-compose.prod.yml service/tests/test_compose_tiers.py
git commit -m "feat(realm): prod two-listener split — unpublished duar-internal :9010"
```

---

### Task 3: Split `authz_router` — browser idp routes public, `/resolve` internal

**Why (added mid-execution, user-approved):** `authz_router` is on the **internal** tier (Task 1), but it also holds `/authz/idp/{provider}/login|callback` — the browser-facing GitHub proxy-login that uses `request.session` for OAuth-state CSRF. The internal tier drops `SessionMiddleware`, so those routes would 500, and internal-only means browsers can't reach them at all. Fix: extract the idp routes into a **public** `idp_router` (which gets Session), keep the service-key `/authz/resolve` on the internal `authz_router`. Both keep the `/authz` prefix.

**Files:**
- Modify: `service/src/api/authz_routes.py` (add `idp_router`; move the two `/idp/*` decorators onto it)
- Modify: `service/src/main.py` (import `idp_router`; add to `PUBLIC_ROUTERS`)
- Modify: `service/tests/test_app_tiers.py` (make the `/authz` tier assertions precise: `/authz/resolve` internal, `/authz/idp/*` public; add a router-placement test)
- Modify: `service/tests/test_authz_github_callback_state.py` (it builds an app with `authz_router` and hits `/authz/idp/github/callback` — now include `idp_router` instead)

**Interfaces:**
- Consumes: `PUBLIC_ROUTERS`/`INTERNAL_ROUTERS`, `create_app` (Task 1).
- Produces: `authz_routes.idp_router` (prefix `/authz`, the `/idp/*` routes); `authz_routes.router` keeps only `/authz/resolve`. `PUBLIC_ROUTERS` gains `authz_idp_router`.

- [ ] **Step 1: Update the tier tests to expect the split (write the failing assertions)**

In `service/tests/test_app_tiers.py`:

(a) In `test_public_tier_has_browser_routes_not_service_key_surface`, replace the service-key-absent block:

```python
    # Service-key surface must be ABSENT from the public listener:
    assert not _has_prefix(app, "/authz")
    assert not _has_prefix(app, "/permissions")
    assert not _has_prefix(app, "/realm")
    assert not _has_prefix(app, "/roles")
```

with:

```python
    # Service-key surface must be ABSENT from the public listener:
    assert "/authz/resolve" not in _paths(app)  # authz SERVICE surface — internal only
    assert not _has_prefix(app, "/permissions")
    assert not _has_prefix(app, "/realm")
    assert not _has_prefix(app, "/roles")
    # ...but the browser-facing GitHub proxy-login IS public (it needs Session):
    assert any(p.startswith("/authz/idp") for p in _paths(app))
```

(b) In `test_internal_tier_has_service_key_surface_not_browser_routes`, replace the body:

```python
    app = create_app("internal")
    assert _has_prefix(app, "/authz")
    assert _has_prefix(app, "/permissions")
    assert _has_prefix(app, "/realm")  # Plan 2 router, mounted here
    assert _has_prefix(app, "/roles")
    # Browser surface must be ABSENT from the internal listener:
    assert not _has_prefix(app, "/auth")  # note: /authz is present, /auth is not
    assert not _has_prefix(app, "/admin")
    assert not _has_prefix(app, "/users")
    assert not _has_prefix(app, "/workspaces")
```

with:

```python
    app = create_app("internal")
    assert "/authz/resolve" in _paths(app)  # authz SERVICE surface — internal
    assert _has_prefix(app, "/permissions")
    assert _has_prefix(app, "/realm")  # Plan 2 router, mounted here
    assert _has_prefix(app, "/roles")
    # Browser surface must be ABSENT from the internal listener:
    assert not _has_prefix(app, "/auth")  # /auth proxy — public
    assert not any(p.startswith("/authz/idp") for p in _paths(app))  # idp proxy — public
    assert not _has_prefix(app, "/admin")
    assert not _has_prefix(app, "/users")
    assert not _has_prefix(app, "/workspaces")
```

(c) In `test_all_tier_is_todays_app_superset`, add two lines after the `for`-loop asserts (the combined app has both halves of authz):

```python
    assert "/authz/resolve" in _paths(app)
    assert any(p.startswith("/authz/idp") for p in _paths(app))
```

(d) Add a new router-placement test (after `test_realm_router_is_internal_only`):

```python
def test_authz_idp_router_is_public_resolve_is_internal():
    """The browser GitHub-proxy router (/authz/idp/*) is public (needs Session); the
    service-key /authz/resolve stays internal — so the network split doesn't strand
    session-using browser routes on the no-Session internal listener."""
    from src.api.authz_routes import idp_router as authz_idp_router
    from src.api.authz_routes import router as authz_router

    assert authz_idp_router in PUBLIC_ROUTERS
    assert authz_idp_router not in INTERNAL_ROUTERS
    assert authz_router in INTERNAL_ROUTERS
    assert authz_router not in PUBLIC_ROUTERS
```

- [ ] **Step 2: Run the tier tests to verify they fail**

Run: `cd service && uv run pytest tests/test_app_tiers.py -v`
Expected: FAIL — `test_authz_idp_router_is_public_resolve_is_internal` errors on `ImportError: cannot import name 'idp_router'`, and the public/internal assertions fail because `/authz/idp/*` is still on the internal `authz_router`.

- [ ] **Step 3: Add `idp_router` and move the idp routes in `authz_routes.py`**

In `service/src/api/authz_routes.py`, immediately after `router = APIRouter(prefix="/authz", tags=["authz"])` (line 36), add:

```python
# Browser-facing GitHub proxy-login (server-side OAuth code exchange) uses
# request.session for OAuth-state CSRF, so it must live on the PUBLIC listener (which
# mounts SessionMiddleware). The service-key /resolve endpoint stays internal. Both
# keep the /authz prefix.
idp_router = APIRouter(prefix="/authz", tags=["authz-idp"])
```

Then change the two idp endpoint decorators (leave the function bodies and the `@limiter.limit(...)` lines untouched):
- `@router.get("/idp/{provider}/login")` → `@idp_router.get("/idp/{provider}/login")`
- `@router.get("/idp/{provider}/callback")` → `@idp_router.get("/idp/{provider}/callback")`

Leave `@router.post("/resolve", ...)` on `router`.

- [ ] **Step 4: Mount `idp_router` on the public tier in `main.py`**

In `service/src/main.py`, add the import next to the existing authz import (the line `from src.api.authz_routes import router as authz_router`):

```python
from src.api.authz_routes import idp_router as authz_idp_router
```

Then add `authz_idp_router` to `PUBLIC_ROUTERS` (right after `auth_router`):

```python
PUBLIC_ROUTERS = [
    admin_router,
    org_admin_router,
    auth_router,
    authz_idp_router,
    user_router,
    workspace_router,
    group_router,
    client_log_router,
]
```

`INTERNAL_ROUTERS` is unchanged — `authz_router` (now `/resolve`-only) stays internal.

- [ ] **Step 5: Fix the GitHub-callback regression test to use `idp_router`**

In `service/tests/test_authz_github_callback_state.py`, the test mounts `authz_router` and hits `/authz/idp/github/callback` — that route now lives on `idp_router`. Change the import (currently `from src.api.authz_routes import router as authz_router`) to:

```python
from src.api.authz_routes import idp_router as authz_idp_router
```

and the `app.include_router(authz_router)` line to:

```python
    app.include_router(authz_idp_router)
```

(If the file references `authz_router` anywhere else, point those at `authz_idp_router` too — the test only exercises the idp callback route.)

- [ ] **Step 6: Run the tier tests to verify they pass**

Run: `cd service && uv run pytest tests/test_app_tiers.py -v`
Expected: PASS (11 passed).

- [ ] **Step 7: Run the authz + full suite (no regression)**

Run: `cd service && uv run pytest tests/test_authz_github_callback_state.py tests/test_authz_resolve_guard.py tests/test_authz_org_gate.py tests/test_realm_authz_minting.py -v`
Expected: PASS — the callback test now finds its route on `idp_router`; the `/resolve` tests still find `/resolve` on `authz_router`.
Then: `cd service && uv run pytest tests/ -q` — confirm no new failures vs baseline (IdP/JWKS **connection** failures are the known sandbox artifact).

- [ ] **Step 8: Lint + commit**

```bash
cd service && uv run ruff format $FILES && uv run ruff check --fix $FILES   # $FILES = ONLY this task's 4 files; never 'ruff format .' / 'make fmt'
git add service/src/api/authz_routes.py service/src/main.py \
  service/tests/test_app_tiers.py service/tests/test_authz_github_callback_state.py
git commit -m "feat(realm): split authz router — /authz/idp (browser) public, /resolve internal"
```

---

## Self-review (done by plan author)

**Spec coverage (Plan 3's slice — "Network posture — hard split"):**
- `create_app(tier)` factory in `main.py` (refactor of `:185-245`) → Task 1.
- `TIER` env → public (published) vs internal (unpublished) → Task 1 (`_resolve_tier`) + Task 2 (compose).
- Internal drops CORS + Session, keeps SecurityHeaders/rate-limiting/RequestContext/AccessLog → Task 1 (`test_internal_tier_drops_session_and_cors_middleware`).
- JWKS public by default → Task 1 (`test_jwks_public_and_all_not_internal`).
- Router audit (realm/permission/authz/role → internal; admin/org-admin/auth/client-log → public; user/workspace/group → public) → Task 1 router groups + `Global Constraints` tier map.
- Swarm: both on the `duar` overlay, `:9010` never published, app services reach `duar-internal:9010` → Task 2.
- **Mounting the `/realm` router (deferred from Plan 2)** → Task 1 (`INTERNAL_ROUTERS`, `test_realm_router_is_internal_only`).
- Spec test "internal routes not on the public app and vice-versa" → Task 1 (`test_public_…`/`test_internal_…`).
- **Deferred (out of Plan 3):** SDK base-URL/internal-listener wiring (Plan 5); the internal-listener deployment-posture **docs** (Plan 6); `service/Dockerfile` `uv.lock` reproducibility gotcha (pre-existing, separate from the split).

**Non-breaking proof:** `create_app("all")` is the `TIER`-unset default and includes every current router + Session + CORS + migrations — Task 1 Step 7 runs the full existing suite (which imports `from src.main import app`) to prove it. The dev `docker-compose.yml` and `make start` set no `TIER` → `all` → unchanged.

**Placeholder scan:** none — every code/command step carries full content. The lifespan change is two exact find-and-replace edits reading `tier` from `app.state.tier` (no re-indentation); the unchanged Redis/security-check body is explicitly "stays as-is", not a placeholder.

**Type consistency:** `create_app(tier: str) -> FastAPI` and `_resolve_tier() -> str` named identically in Task 1 code and its tests; `PUBLIC_ROUTERS`/`INTERNAL_ROUTERS` defined in Task 1, asserted in `test_realm_router_is_internal_only`. The `TIER` values `"public"`/`"internal"`/`"all"` are consistent across `_resolve_tier`, `create_app`, and the compose `environment` in Task 2. The `&duar_env` anchor defined in Task 2 Step 3 is referenced via `<<: *duar_env` in Step 4.

**Known integration gaps (call out at execution):** lifespan behavior (tier-gated migrations, CORS warm) runs only at real startup (`make start` / container boot), not in the construction-time unit tests — the tests prove router/middleware membership, the most security-relevant invariant. `docker compose config` (Task 2 Step 6) needs Docker; the YAML-level test (Step 5) is the portable gate.
