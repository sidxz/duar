# Rate-Limit Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse three overlapping rate-limit mechanisms into a single, fully config-driven slowapi limiter — fixing the compounding 429s, the prod outage, and the IP-collision throttling.

**Architecture:** slowapi becomes the *sole* rate limiter. The hand-rolled `GlobalRateLimitMiddleware` and its in-memory fallback are deleted. The opt-in aggregate/volumetric cap is slowapi `application_limits`; the long-tail default is `application`/`default_limits` enforced by `SlowAPIASGIMiddleware` (the ASGI variant — it preserves our async `ratelimit.exceeded` logging handler, which the BaseHTTPMiddleware variant silently drops). The 22 hardcoded per-route limits become config tiers. Authenticated routes key on the resolved subject (`request.state.actor`), `/authz/resolve` keys on the calling service (`request.state.caller_service`), login stays IP-keyed — all read from state that `bind_identity()` already populates, so **no auth-dependency changes are needed**. Redis errors fail **open** (`swallow_errors=True`).

**Tech Stack:** Python 3.12 · FastAPI · slowapi 0.1.10 · limits 5.8.0 · Redis 7 · pydantic-settings · pytest · uv workspaces · MkDocs Material.

## Global Constraints

- **slowapi 0.1.10 coverage model (verified against installed source):**
  - A route **with** a static `@limiter.limit(...)` decorator → gets **only** that decorator's limit. `SlowAPIASGIMiddleware` exempts it (`middleware.py` `_should_exempt`: *"there is a decorator for this route we let the decorator handle it"*), so `application_limits`/`default_limits` do **not** apply, and the decorator's check runs in the endpoint wrapper **after** FastAPI resolves dependencies.
  - A route **without** a decorator → gets `application_limits` (per-IP, `"global"` scope) + `default_limits` (per-IP, per-route), enforced by the middleware **before** auth.
- **Limit values must be static strings** (`@limiter.limit(settings.rate_limit_x)`), read at import. A *callable* limit value lands the route in `_dynamic_route_limits`, silently un-exempting it from the middleware and changing which limits apply. Do not use callables.
- **ACCEPTED TRADEOFF (must be documented loudly):** app-layer limiting gives **no** volumetric DoS protection and **no** pre-auth throttling for decorated routes (`/authz/resolve`, `/auth/token`, admin POSTs). Edge rate limiting (nginx/Cloudflare/ALB) is required for that. This was an explicit decision.
- **Fail open:** `swallow_errors=True`. A storage (Redis) error must let the request through, never 500 or throttle. Real limit breaches still return 429.
- **Tests:** run from `service/` with `cd service && uv run pytest`. There is **no `conftest.py`** — each test file is standalone and disables limiting via a fresh `Limiter(enabled=False)` or `limiter.enabled = False`. Use `storage_uri="memory://"` in tests; never depend on a live Redis.
- **Lint/format:** run `make fmt` (ruff) before every commit. Config uses `extra="ignore"`, so a leftover `RATE_LIMIT_RPM` env var is harmless after removal.
- **Docs:** `docs.yml` CI runs `mkdocs --strict`; doc edits must keep the build clean (no broken links/refs).
- **Keep unchanged:** `get_client_ip` (rightmost-hop XFF logic) and `rate_limit_exceeded_handler` (the `ratelimit.exceeded` logger). `test_rate_limit_xff.py` must keep passing untouched.

---

## File Structure

**Modify:**
- `service/src/config.py` — add 8 rate-limit tier settings; remove `rate_limit_rpm` (Task 3).
- `service/src/middleware/rate_limit.py` — add `user_or_ip_key`, `service_or_ip_key`, `rate_limit_report`; reconfigure `limiter`; delete `GlobalRateLimitMiddleware`, in-memory fallback, `_get_redis`/`_redis`, `_INCR_WITH_EXPIRE`.
- `service/src/main.py` — drop `GlobalRateLimitMiddleware`; add `SlowAPIASGIMiddleware`.
- `service/src/api/{auth,authz,permission,workspace,group,client_log,org_admin,admin}_routes.py` — replace 22 literal limits with config tiers + per-route `key_func`.
- `service/src/api/admin_routes.py` — replace the stale hardcoded `rate_limits` self-report with `rate_limit_report()`.
- `docker-compose.yml`, `docker-compose.prod.yml`, `docker-compose.pentest.yml` — replace `RATE_LIMIT_RPM`; prod gains `TRUSTED_PROXY_COUNT` + aggregate.
- `docs/getting-started/configuration.md`, `docs/security.md` — document new vars + the accepted tradeoff.

**Create:**
- `service/tests/test_rate_limit_config.py` — tier strings parse.
- `service/tests/test_rate_limit_tiers.py` — keying isolation, fail-open config, route wiring, self-report.

**Rewrite:**
- `service/tests/test_rate_limit_disable.py`, `service/tests/test_ratelimit_event.py` — drop deleted-mechanism tests.

---

## Task 1: Config tiers (additive)

**Files:**
- Modify: `service/src/config.py` (after line 74, the `trusted_proxy_count` block; keep `rate_limit_rpm` for now)
- Test: `service/tests/test_rate_limit_config.py`

**Interfaces:**
- Produces: `settings.rate_limit_default`, `.rate_limit_aggregate`, `.rate_limit_auth`, `.rate_limit_auth_admin`, `.rate_limit_authz_resolve`, `.rate_limit_read`, `.rate_limit_admin_write`, `.rate_limit_sensitive` — all `str` (limits-library format, e.g. `"10/minute"`; `""` disables that tier).

- [ ] **Step 1: Write the failing test**

Create `service/tests/test_rate_limit_config.py`:

```python
"""Every configured rate-limit tier must be a string the `limits` library accepts.
Guards against typos like "10/min" (valid format is "10/minute") that would
otherwise blow up at request time inside slowapi.
"""
from limits import parse

from src.config import settings

TIERS = [
    "rate_limit_default",
    "rate_limit_aggregate",
    "rate_limit_auth",
    "rate_limit_auth_admin",
    "rate_limit_authz_resolve",
    "rate_limit_read",
    "rate_limit_admin_write",
    "rate_limit_sensitive",
]


def test_all_tiers_exist_and_parse():
    for name in TIERS:
        value = getattr(settings, name)
        assert isinstance(value, str), name
        if value:  # "" is the explicit "disable this tier" duar
            parse(value)  # raises ValueError on a malformed limit string
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_rate_limit_config.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'rate_limit_default'`.

- [ ] **Step 3: Add the settings**

In `service/src/config.py`, immediately after the `trusted_proxy_count` field (the closing `)` on line ~74) and before the `# Admin` comment, insert:

```python
    # Rate-limit tiers — limits-library strings ("10/minute"); "" disables a tier.
    # Read at import time (restart to change). See middleware/rate_limit.py for the
    # slowapi coverage model and docs/security.md for the edge-rate-limiting caveat.
    rate_limit_default: str = "120/minute"  # per IP, per undecorated route (long tail)
    rate_limit_aggregate: str = (
        "300/minute"  # per IP across all undecorated routes (volumetric ceiling)
    )
    rate_limit_auth: str = "10/minute"  # login/callback/token/refresh + authz idp (per IP)
    rate_limit_auth_admin: str = "5/minute"  # admin login/callback (per IP)
    rate_limit_authz_resolve: str = "60/minute"  # POST /authz/resolve (per calling service)
    rate_limit_read: str = "60/minute"  # authenticated reads (per user)
    rate_limit_admin_write: str = "10/minute"  # admin mutations (per user)
    rate_limit_sensitive: str = "5/minute"  # destructive/expensive admin ops (per user)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd service && uv run pytest tests/test_rate_limit_config.py -v`
Expected: PASS (2 assertions, all tiers parse).

- [ ] **Step 5: Commit**

```bash
make fmt
git add service/src/config.py service/tests/test_rate_limit_config.py
git commit -m "feat(ratelimit): add config-driven rate-limit tiers"
```

---

## Task 2: Limiter reconfig + key functions + keying tests (additive)

**Files:**
- Modify: `service/src/middleware/rate_limit.py` (add functions; reconfigure `limiter` at lines 44-49; do **not** delete anything yet)
- Test: `service/tests/test_rate_limit_tiers.py`

**Interfaces:**
- Consumes: `settings.rate_limit_*` (Task 1); existing `get_client_ip`.
- Produces:
  - `user_or_ip_key(request: Request) -> str` — `"user:<actor>"` if `request.state.actor` set, else `"ip:<ip>"`.
  - `service_or_ip_key(request: Request) -> str` — `"svc:<caller_service>"` if set, else `"ip:<ip>"`.
  - `limiter` now built with `default_limits`, `application_limits`, `swallow_errors=True`.

- [ ] **Step 1: Write the failing test**

Create `service/tests/test_rate_limit_tiers.py`:

```python
"""slowapi keying: authenticated routes bucket per user/service, not per IP, so
one busy actor (or a shared NAT/proxy IP) can't throttle everyone else. Also
asserts the live limiter is configured fail-open.

Uses fresh in-memory Limiters so the suite never touches Redis and never mutates
the module singleton's route registry.
"""
from fastapi import Depends, FastAPI, Request
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.testclient import TestClient

import src.middleware.rate_limit as rl


def _client(*, key_func, identity_header, field):
    lim = Limiter(key_func=rl.get_client_ip, storage_uri="memory://", swallow_errors=True)
    app = FastAPI()
    app.state.limiter = lim
    app.add_exception_handler(RateLimitExceeded, rl.rate_limit_exceeded_handler)

    async def bind(request: Request):
        # Mirror bind_identity(): write the identity into scope state, which
        # request.state reads, BEFORE the decorator's check runs in the wrapper.
        val = request.headers.get(identity_header)
        if val:
            request.scope.setdefault("state", {})[field] = val

    @app.get("/r")
    @lim.limit("2/minute", key_func=key_func)
    async def r(request: Request, _: None = Depends(bind)):
        return {"ok": True}

    return TestClient(app)


def test_per_user_keying_isolates_users():
    c = _client(key_func=rl.user_or_ip_key, identity_header="X-Actor", field="actor")
    assert c.get("/r", headers={"X-Actor": "alice"}).status_code == 200
    assert c.get("/r", headers={"X-Actor": "alice"}).status_code == 200
    assert c.get("/r", headers={"X-Actor": "alice"}).status_code == 429  # alice exhausted
    assert c.get("/r", headers={"X-Actor": "bob"}).status_code == 200  # bob unaffected


def test_per_service_keying_isolates_services():
    c = _client(key_func=rl.service_or_ip_key, identity_header="X-Service", field="caller_service")
    assert c.get("/r", headers={"X-Service": "svc-a"}).status_code == 200
    assert c.get("/r", headers={"X-Service": "svc-a"}).status_code == 200
    assert c.get("/r", headers={"X-Service": "svc-a"}).status_code == 429
    assert c.get("/r", headers={"X-Service": "svc-b"}).status_code == 200


def test_falls_back_to_ip_without_identity():
    c = _client(key_func=rl.user_or_ip_key, identity_header="X-Actor", field="actor")
    assert c.get("/r").status_code == 200
    assert c.get("/r").status_code == 200
    assert c.get("/r").status_code == 429  # same TestClient IP shares one bucket


def test_singleton_limiter_is_fail_open():
    # A Redis blip must let traffic through, not 500 or throttle.
    assert rl.limiter._swallow_errors is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_rate_limit_tiers.py -v`
Expected: FAIL — `AttributeError: module 'src.middleware.rate_limit' has no attribute 'user_or_ip_key'` (and `_swallow_errors is True` fails — current limiter omits it).

- [ ] **Step 3: Add the key functions**

In `service/src/middleware/rate_limit.py`, immediately after `get_client_ip` (after line 41), add:

```python
def user_or_ip_key(request: Request) -> str:
    """Rate-limit key for authenticated endpoints: bucket by user, fall back to IP.

    ``bind_identity`` (request_context) writes the authenticated subject to
    ``request.state.actor`` during dependency resolution, which runs *before*
    slowapi's per-route check fires in the endpoint wrapper — so the actor is
    available here. IP fallback keeps the limit meaningful on any path where no
    user is resolved.
    """
    actor = getattr(request.state, "actor", None)
    if actor:
        return f"user:{actor}"
    return f"ip:{get_client_ip(request)}"


def service_or_ip_key(request: Request) -> str:
    """Rate-limit key for service-to-service endpoints: bucket by calling service.

    Used by POST /authz/resolve, where no user exists at dependency time (the IdP
    token is in the request body and the user is provisioned inside the handler).
    ``require_service_context`` binds ``caller_service`` before the check fires.
    """
    svc = getattr(request.state, "caller_service", None)
    if svc:
        return f"svc:{svc}"
    return f"ip:{get_client_ip(request)}"
```

- [ ] **Step 4: Reconfigure the limiter**

In `service/src/middleware/rate_limit.py`, replace the `limiter = Limiter(...)` block (lines 44-49) with:

```python
limiter = Limiter(
    key_func=get_client_ip,
    default_limits=[settings.rate_limit_default] if settings.rate_limit_default else [],
    application_limits=(
        [settings.rate_limit_aggregate] if settings.rate_limit_aggregate else []
    ),
    storage_uri=settings.redis_url,
    storage_options=settings.redis_ssl_kwargs,
    swallow_errors=True,  # fail OPEN: a Redis blip must not 500 or throttle legit traffic
    enabled=settings.rate_limit_enabled,  # master switch (False on ephemeral pentest targets)
)
```

(Leave `GlobalRateLimitMiddleware`, the fallback, and `_get_redis` in place for now — they are removed in Task 3. They are currently inert against the new limiter and the app still wires them, so the suite stays green.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd service && uv run pytest tests/test_rate_limit_tiers.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Run the full suite to confirm no regression**

Run: `cd service && uv run pytest -q`
Expected: PASS (additive change; existing tests unaffected).

- [ ] **Step 7: Commit**

```bash
make fmt
git add service/src/middleware/rate_limit.py service/tests/test_rate_limit_tiers.py
git commit -m "feat(ratelimit): per-user/per-service key funcs; fail-open limiter with default+aggregate limits"
```

---

## Task 3: Swap to SlowAPIASGIMiddleware; delete GlobalRateLimitMiddleware (subtractive)

**Files:**
- Modify: `service/src/main.py` (imports lines 24-29; middleware add lines 200-203)
- Modify: `service/src/middleware/rate_limit.py` (delete dead code + unused imports; expand the module docstring)
- Modify: `service/src/config.py` (remove `rate_limit_rpm`)
- Rewrite: `service/tests/test_rate_limit_disable.py`, `service/tests/test_ratelimit_event.py`

**Interfaces:**
- Consumes: reconfigured `limiter`, `rate_limit_exceeded_handler` (unchanged signature).
- Produces: `GlobalRateLimitMiddleware`, `_get_redis`, `_redis`, `_INCR_WITH_EXPIRE`, `_fallback_*`, `_FALLBACK_*`, and `settings.rate_limit_rpm` no longer exist.

- [ ] **Step 1: Rewrite the two affected test files first (they pin the old mechanism)**

Replace the entire contents of `service/tests/test_rate_limit_disable.py` with:

```python
"""Rate limiting must be disableable for the ephemeral pentest target.

The Layer-2 isolation prover drives many OAuth logins in a burst, tripping the
per-route limits. For a throwaway, allowlisted target the limiter is bypassable
via RATE_LIMIT_ENABLED=false — without weakening the production default (on).
Fail-safe: the field defaults to True and the Limiter is constructed with
enabled=that value, so slowapi short-circuits every check when off.
"""
from fastapi import FastAPI, Request
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.testclient import TestClient

import src.middleware.rate_limit as rl
from src.config import settings


def test_disabled_limiter_bypasses_checks():
    lim = Limiter(key_func=rl.get_client_ip, storage_uri="memory://", enabled=False)
    app = FastAPI()
    app.state.limiter = lim
    app.add_exception_handler(RateLimitExceeded, rl.rate_limit_exceeded_handler)

    @app.get("/probe")
    @lim.limit("1/minute")
    async def probe(request: Request):
        return {"ok": True}

    client = TestClient(app)
    # Well past 1/minute; every call passes because the limiter is off (no store hit).
    for _ in range(5):
        assert client.get("/probe").status_code == 200


def test_singleton_limiter_honors_enabled_setting():
    assert rl.limiter.enabled == settings.rate_limit_enabled


def test_rate_limiting_enabled_by_default():
    assert settings.rate_limit_enabled is True
```

Replace the entire contents of `service/tests/test_ratelimit_event.py` with:

```python
"""The 429 path must still emit the ``ratelimit.exceeded`` security event.

With GlobalRateLimitMiddleware removed, slowapi is the only source of 429s; the
shared ``rate_limit_exceeded_handler`` logs the event for every limit (route,
default, and aggregate).
"""
import pytest
from starlette.requests import Request
from structlog.testing import capture_logs

import src.middleware.rate_limit as rl


def _request(path="/admin/x", ip="10.0.0.9"):
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "client": (ip, 5555),
            "server": ("t", 80),
        }
    )


@pytest.mark.asyncio
async def test_handler_emits_ratelimit_exceeded():
    # Build a REAL RateLimitExceeded — the handler reads exc.limit.limit.get_expiry()
    # for Retry-After (slowapi's exception has no .retry_after). A fake stub would
    # mask that path (this is the bug a fake masked in the original handler test).
    from limits import parse
    from slowapi.errors import RateLimitExceeded
    from slowapi.wrappers import Limit

    exc = RateLimitExceeded(
        Limit(parse("5/minute"), lambda: "k", None, False, None, None, None, 1, True)
    )
    with capture_logs() as logs:
        resp = await rl.rate_limit_exceeded_handler(_request(), exc)

    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) > 0
    events = [e for e in logs if e["event"] == "ratelimit.exceeded"]
    assert len(events) == 1
    assert events[0]["outcome"] == "denied"
    assert events[0]["http.route"] == "/admin/x"
    assert events[0]["source_ip"] == "10.0.0.9"
```

- [ ] **Step 2: Run the two rewritten files**

Run: `cd service && uv run pytest tests/test_rate_limit_disable.py tests/test_ratelimit_event.py -v`
Expected: PASS. (The rewrites no longer reference the soon-to-be-deleted `GlobalRateLimitMiddleware`/`_get_redis`/`_fallback_*`, so they pass against both current and post-deletion code. The deletion itself is gated by the full suite in Step 6.)

- [ ] **Step 3: Delete the dead code in `rate_limit.py`**

In `service/src/middleware/rate_limit.py`, delete:
- the in-memory fallback module globals (lines 16-20: `_fallback_counts`, `_FALLBACK_WINDOW`, `_FALLBACK_LIMIT`, `_fallback_request_count`),
- the Redis singleton + getter (lines 69-78: `_redis`, `_get_redis`),
- the Lua script (lines 81-89: `_INCR_WITH_EXPIRE`),
- the entire `GlobalRateLimitMiddleware` class (lines 92-161).

Then remove now-unused imports at the top: `import time`, `from collections import defaultdict`, `import redis.asyncio as aioredis`, `from starlette.middleware.base import BaseHTTPMiddleware`, and `Response` from the starlette responses import (keep `JSONResponse`).

Replace the module docstring (line 1) with:

```python
"""Rate limiting via slowapi (Redis-backed). slowapi is the SOLE mechanism.

Coverage model (slowapi 0.1.10 — verified against the installed source):
  * Routes WITHOUT a decorator → ``application_limits`` (per-IP aggregate,
    "global" scope) + ``default_limits`` (per-IP, per-route), enforced by
    ``SlowAPIASGIMiddleware`` BEFORE auth/dependencies run.
  * Routes WITH an ``@limiter.limit(...)`` decorator → ONLY that decorator's
    limit. slowapi intentionally EXEMPTS decorated routes from the middleware,
    so aggregate/default limits do NOT apply, and the decorator's check runs in
    the endpoint wrapper AFTER dependencies — a request rejected by an auth
    ``Depends`` (bad token/service key) is therefore not throttled here.

ACCEPTED TRADEOFF: app-layer limiting provides NO volumetric DoS protection and
NO pre-auth throttling for decorated routes (/authz/resolve, /auth/token, admin
POSTs). Deploy EDGE rate limiting (nginx/Cloudflare/ALB) for that. See
docs/security.md.

Fail-open: ``swallow_errors=True`` — a storage (Redis) error lets the request
through rather than 500ing or throttling. Real limit breaches still 429.
"""
```

- [ ] **Step 4: Swap the middleware in `main.py`**

In `service/src/main.py`:

Change the rate-limit import block (lines 24-28) from:

```python
from src.middleware.rate_limit import (
    GlobalRateLimitMiddleware,
    limiter,
    rate_limit_exceeded_handler,
)
```

to:

```python
from src.middleware.rate_limit import (
    limiter,
    rate_limit_exceeded_handler,
)
from slowapi.middleware import SlowAPIASGIMiddleware
```

Replace the middleware registration (lines 200-203) from:

```python
# Global rate limiting (configurable via RATE_LIMIT_RPM, default 30 req/min per IP)
app.add_middleware(
    GlobalRateLimitMiddleware, requests_per_minute=settings.rate_limit_rpm
)
```

with:

```python
# Rate limiting (slowapi). The ASGI variant preserves our async ratelimit.exceeded
# handler; the BaseHTTPMiddleware variant would silently fall back to slowapi's
# default handler (no security log). Decorated routes are exempt here and enforce
# their own per-route tier in the endpoint wrapper. See middleware/rate_limit.py.
app.add_middleware(SlowAPIASGIMiddleware)
```

- [ ] **Step 5: Remove `rate_limit_rpm` from config**

In `service/src/config.py`, delete the `rate_limit_rpm` field (line 63: `rate_limit_rpm: int = 30  # Global rate limit (requests per minute per IP)`). Keep `rate_limit_enabled`, `behind_proxy`, `trusted_proxy_count`.

- [ ] **Step 6: Run the full suite**

Run: `cd service && uv run pytest -q`
Expected: PASS. In particular `test_rate_limit_disable.py`, `test_ratelimit_event.py`, `test_rate_limit_tiers.py`, `test_rate_limit_xff.py` all green, and no `ImportError`/`AttributeError` for `GlobalRateLimitMiddleware`, `_get_redis`, `rate_limit_rpm`.

- [ ] **Step 7: Commit**

```bash
make fmt
git add service/src/main.py service/src/middleware/rate_limit.py service/src/config.py \
        service/tests/test_rate_limit_disable.py service/tests/test_ratelimit_event.py
git commit -m "refactor(ratelimit): delete GlobalRateLimitMiddleware; slowapi is the sole limiter"
```

---

## Task 4: Apply config tiers + keying to the 22 per-route limits

**Files (modify all):**
- `service/src/api/auth_routes.py`, `authz_routes.py`, `permission_routes.py`, `workspace_routes.py`, `group_routes.py`, `client_log_routes.py`, `org_admin_routes.py`, `admin_routes.py`
- Test: `service/tests/test_rate_limit_tiers.py` (append wiring test)

**Interfaces:**
- Consumes: `settings.rate_limit_*`, `user_or_ip_key`, `service_or_ip_key` from `src.middleware.rate_limit`.

**Mapping (match by function/decorator, not just line number — numbers shift as edits land):**

| File · function | Old | New tier | `key_func` |
|---|---|---|---|
| auth · login | `"10/minute"` | `settings.rate_limit_auth` | — (IP) |
| auth · callback | `"10/minute"` | `settings.rate_limit_auth` | — |
| auth · `list_workspaces_for_login` | `"10/minute"` | `settings.rate_limit_auth` | — |
| auth · `select_workspace_and_issue_tokens` | `"10/minute"` | `settings.rate_limit_auth` | — |
| auth · refresh | `"10/minute"` | `settings.rate_limit_auth` | — |
| auth · admin login | `"5/minute"` | `settings.rate_limit_auth_admin` | — |
| auth · admin callback | `"5/minute"` | `settings.rate_limit_auth_admin` | — |
| authz · `idp_login` | `"10/minute"` | `settings.rate_limit_auth` | — |
| authz · `idp_callback` | `"10/minute"` | `settings.rate_limit_auth` | — |
| authz · `resolve` | `"10/minute"` | `settings.rate_limit_authz_resolve` | `service_or_ip_key` |
| permission · enriched ACL | `"30/minute"` | `settings.rate_limit_read` | `user_or_ip_key` |
| workspace · members | `"60/minute"` | `settings.rate_limit_read` | `user_or_ip_key` |
| group · members | `"60/minute"` | `settings.rate_limit_read` | `user_or_ip_key` |
| client_log · `ingest_client_logs` | `"60/minute"` | `settings.rate_limit_read` | `user_or_ip_key` |
| org_admin · create org | `"5/minute"` | `settings.rate_limit_sensitive` | `user_or_ip_key` |
| org_admin · add domain | `"10/minute"` | `settings.rate_limit_admin_write` | `user_or_ip_key` |
| admin · `bulk_user_status` | `"5/minute"` | `settings.rate_limit_sensitive` | `user_or_ip_key` |
| admin · revoke-tokens | `"10/minute"` | `settings.rate_limit_admin_write` | `user_or_ip_key` |
| admin · purge service perms | `"5/minute"` | `settings.rate_limit_sensitive` | `user_or_ip_key` |
| admin · create service-app | `"5/minute"` | `settings.rate_limit_sensitive` | `user_or_ip_key` |
| admin · csv preview | (was undecorated) | `settings.rate_limit_sensitive` | `user_or_ip_key` |
| admin · csv execute | `"5/minute"` | `settings.rate_limit_sensitive` | `user_or_ip_key` |
| admin · `rotate_service_app_key` | `"3/minute"` | `settings.rate_limit_sensitive` | `user_or_ip_key` |

> Correction: an earlier draft mislabeled `admin_routes.py:1380` as "csv preview". That line was always `rotate_service_app_key`; `csv_preview` was undecorated and is newly rate-limited here. 23 decorated routes total.

**Behavior changes to note in the commit body:** enriched 30→60, csv-preview 3→5, authz-resolve 10→60 (loosenings from tier consolidation + hot-path relief). The substantive fix is the keying change (IP → user/service) on every authenticated route.

- [ ] **Step 1: Write the failing wiring test**

Append to `service/tests/test_rate_limit_tiers.py`:

```python
def test_route_keying_is_wired():
    # Importing main registers every router, applying the decorators onto the
    # module-singleton limiter's route registry.
    import src.main  # noqa: F401

    all_limits = [lim for lims in rl.limiter._route_limits.values() for lim in lims]
    # /authz/resolve must be service-keyed.
    assert any(lim.key_func is rl.service_or_ip_key for lim in all_limits)
    # The authenticated read/write/sensitive routes must be user-keyed (>=10 of them).
    user_keyed = [lim for lim in all_limits if lim.key_func is rl.user_or_ip_key]
    assert len(user_keyed) >= 10
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd service && uv run pytest tests/test_rate_limit_tiers.py::test_route_keying_is_wired -v`
Expected: FAIL — no route uses `service_or_ip_key`/`user_or_ip_key` yet.

- [ ] **Step 3: Edit imports in each route file**

In each of the 8 route files, ensure the rate_limit import line pulls in the key funcs it needs and that `settings` is imported:

- `auth_routes.py`: no change (IP-only; `settings` already imported line 14).
- `authz_routes.py`: change `from src.middleware.rate_limit import ... limiter ...` to also import `service_or_ip_key`. Confirm `from src.config import settings` is present (it is — used for `github_client_id`).
- `permission_routes.py`, `workspace_routes.py`, `group_routes.py`, `org_admin_routes.py`, `admin_routes.py`: add `user_or_ip_key` to the `from src.middleware.rate_limit import ...` line; confirm `from src.config import settings` is present (add if missing).
- `client_log_routes.py`: change line 11 `from src.middleware.rate_limit import get_client_ip, limiter` to `from src.middleware.rate_limit import get_client_ip, limiter, user_or_ip_key`, and add `from src.config import settings` (currently **absent**).

- [ ] **Step 4: Replace each decorator per the mapping table**

For IP-keyed routes, replace e.g. `@limiter.limit("10/minute")` → `@limiter.limit(settings.rate_limit_auth)`.

For keyed routes, add the `key_func`, e.g.:
- authz resolve: `@limiter.limit("10/minute")` → `@limiter.limit(settings.rate_limit_authz_resolve, key_func=service_or_ip_key)`
- workspace members: `@limiter.limit("60/minute")` → `@limiter.limit(settings.rate_limit_read, key_func=user_or_ip_key)`
- admin bulk-status: `@limiter.limit("5/minute")` → `@limiter.limit(settings.rate_limit_sensitive, key_func=user_or_ip_key)`

**Caution — do NOT blind replace-all by limit string:** `authz_routes.py` has three `"10/minute"` decorators that map to *different* targets — `idp_login` and `idp_callback` → `settings.rate_limit_auth` (no key_func), but `resolve` → `settings.rate_limit_authz_resolve` **with** `key_func=service_or_ip_key`. Edit per function, not per string. (Within `auth_routes.py` and `admin_routes.py` the duplicated strings *do* all map to the same target, so a per-file replace is safe there.)

Apply all 22 rows from the table.

- [ ] **Step 5: Run the wiring test + full suite**

Run: `cd service && uv run pytest tests/test_rate_limit_tiers.py -v && cd service && uv run pytest -q`
Expected: PASS. (If any route module is missing the `settings` or key-func import, you'll get an `ImportError`/`NameError` here — fix the import.)

- [ ] **Step 6: Commit**

```bash
make fmt
git add service/src/api/*.py
git commit -m "feat(ratelimit): config-driven tiers + per-user/per-service keying on all routes

enriched 30->60, csv-preview 3->5, authz-resolve 10->60; authed routes now key
on actor/caller_service instead of IP, ending shared-NAT collateral throttling."
```

---

## Task 5: Fix the stale admin self-report

**Files:**
- Modify: `service/src/middleware/rate_limit.py` (add `rate_limit_report`)
- Modify: `service/src/api/admin_routes.py` (use it; lines 216-221)
- Test: `service/tests/test_rate_limit_tiers.py` (append)

**Interfaces:**
- Produces: `rate_limit_report() -> list[dict[str, str]]` — `[{"endpoint": str, "limit": str}, ...]`, coerced into `list[RateLimitInfo]` by `SystemSettingsResponse`.

- [ ] **Step 1: Write the failing test**

Append to `service/tests/test_rate_limit_tiers.py`:

```python
def test_rate_limit_report_reflects_live_settings(monkeypatch):
    monkeypatch.setattr(rl.settings, "rate_limit_read", "77/minute")
    report = rl.rate_limit_report()
    read = next(r for r in report if "reads" in r["endpoint"])
    assert read["limit"] == "77/minute"


def test_rate_limit_report_shows_disabled_aggregate(monkeypatch):
    monkeypatch.setattr(rl.settings, "rate_limit_aggregate", "")
    report = rl.rate_limit_report()
    agg = next(r for r in report if r["endpoint"].startswith("aggregate"))
    assert agg["limit"] == "disabled"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd service && uv run pytest tests/test_rate_limit_tiers.py -k report -v`
Expected: FAIL — `module 'src.middleware.rate_limit' has no attribute 'rate_limit_report'`.

- [ ] **Step 3: Add `rate_limit_report`**

In `service/src/middleware/rate_limit.py`, after `service_or_ip_key`, add:

```python
def rate_limit_report() -> list[dict[str, str]]:
    """Live view of the active limit tiers for the admin Settings panel.

    Replaces the old hardcoded list, which had drifted from the real decorators.
    """
    return [
        {"endpoint": "aggregate · per IP · all undecorated routes",
         "limit": settings.rate_limit_aggregate or "disabled"},
        {"endpoint": "default · per IP · per undecorated route",
         "limit": settings.rate_limit_default or "disabled"},
        {"endpoint": "auth (login/callback/token/refresh) · per IP",
         "limit": settings.rate_limit_auth},
        {"endpoint": "admin auth (login/callback) · per IP",
         "limit": settings.rate_limit_auth_admin},
        {"endpoint": "authz resolve · per service",
         "limit": settings.rate_limit_authz_resolve},
        {"endpoint": "authenticated reads · per user",
         "limit": settings.rate_limit_read},
        {"endpoint": "admin writes · per user",
         "limit": settings.rate_limit_admin_write},
        {"endpoint": "sensitive admin ops · per user",
         "limit": settings.rate_limit_sensitive},
    ]
```

- [ ] **Step 4: Use it in `admin_routes.py`**

Add to the imports near the other `src.middleware` imports: `from src.middleware.rate_limit import rate_limit_report`.

Replace lines 216-221:

```python
    # Rate limits (hardcoded from decorators)
    rate_limits = [
        {"endpoint": "POST /auth/*/callback", "limit": "5/minute"},
        {"endpoint": "POST /auth/refresh", "limit": "10/minute"},
        {"endpoint": "POST /auth/logout", "limit": "5/minute"},
    ]
```

with:

```python
    # Rate limits — reported live from config (see middleware/rate_limit.py).
    rate_limits = rate_limit_report()
```

- [ ] **Step 5: Run tests**

Run: `cd service && uv run pytest tests/test_rate_limit_tiers.py -v && cd service && uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
make fmt
git add service/src/middleware/rate_limit.py service/src/api/admin_routes.py service/tests/test_rate_limit_tiers.py
git commit -m "fix(admin): report real rate-limit tiers in System Settings (was stale/fabricated)"
```

---

## Task 6: Compose env, prod outage fix, and docs

**Files:**
- Modify: `docker-compose.yml`, `docker-compose.prod.yml`, `docker-compose.pentest.yml`
- Modify: `docs/getting-started/configuration.md`, `docs/security.md`

- [ ] **Step 1: Find every committed `RATE_LIMIT_RPM` reference**

Run: `rg -n 'RATE_LIMIT_RPM' docker-compose*.yml docs ./*.env* 2>/dev/null`
Expected: hits in `docker-compose.yml` (dev, `=10000`) and possibly env templates.

- [ ] **Step 2: Dev compose**

In `docker-compose.yml`, remove the `RATE_LIMIT_RPM: 10000` line. (Defaults are workable for dev; to disable entirely for load testing, set `RATE_LIMIT_ENABLED: "false"`.) Add a brief comment to that effect.

- [ ] **Step 3: Prod compose — the outage fix**

In `docker-compose.prod.yml`, in the `duar` service `environment:` block (after `BEHIND_PROXY: "true"`, line 105), add:

```yaml
      # Number of trusted proxy hops in front of Duar (CDN/LB + nginx = 2, etc.).
      # MUST match reality or all clients collapse into one IP bucket. Verify per deploy.
      TRUSTED_PROXY_COUNT: ${TRUSTED_PROXY_COUNT:-1}
      # Rate-limit tiers (tunable without an image rebuild). Aggregate is the per-IP
      # volumetric ceiling for undecorated routes; "" disables it. NOTE: decorated
      # routes are NOT covered by the aggregate — deploy edge rate limiting for
      # volumetric/bad-auth protection. See docs/security.md.
      RATE_LIMIT_AGGREGATE: ${RATE_LIMIT_AGGREGATE:-300/minute}
      RATE_LIMIT_DEFAULT: ${RATE_LIMIT_DEFAULT:-120/minute}
      RATE_LIMIT_AUTHZ_RESOLVE: ${RATE_LIMIT_AUTHZ_RESOLVE:-60/minute}
```

- [ ] **Step 4: Pentest compose**

Confirm `docker-compose.pentest.yml` sets `RATE_LIMIT_ENABLED: "false"` (the throwaway target disables limiting). If it instead relied on `RATE_LIMIT_RPM`, replace that with `RATE_LIMIT_ENABLED: "false"`.

- [ ] **Step 5: Config reference docs**

In `docs/getting-started/configuration.md`, remove any `RATE_LIMIT_RPM` row and add rows for: `RATE_LIMIT_ENABLED`, `RATE_LIMIT_AGGREGATE`, `RATE_LIMIT_DEFAULT`, `RATE_LIMIT_AUTH`, `RATE_LIMIT_AUTH_ADMIN`, `RATE_LIMIT_AUTHZ_RESOLVE`, `RATE_LIMIT_READ`, `RATE_LIMIT_ADMIN_WRITE`, `RATE_LIMIT_SENSITIVE`, and `TRUSTED_PROXY_COUNT` (if absent) — each with its default and one-line description, matching the existing table format.

- [ ] **Step 6: Security docs — the loud tradeoff**

In `docs/security.md`, update the rate-limiting section to describe: slowapi as the single mechanism; the tier set; per-user/per-service/IP keying; fail-open on Redis; and a clearly-marked callout:

> **Volumetric DoS protection requires edge rate limiting.** The app-layer aggregate (`RATE_LIMIT_AGGREGATE`) only covers routes without a per-route limit; decorated routes (`/authz/resolve`, `/auth/token`, admin POSTs) enforce their own tier but are not in the aggregate and are not throttled before authentication. Put nginx/Cloudflare/ALB rate limiting in front of Duar for volumetric/bad-auth defense.

- [ ] **Step 7: Verify docs build**

Run: `make docs-serve` briefly, or the strict build the CI uses: `uv run mkdocs build --strict` (from the repo root or wherever `mkdocs.yml` lives).
Expected: build succeeds, no warnings/broken refs.

- [ ] **Step 8: Commit**

```bash
make fmt
git add docker-compose.yml docker-compose.prod.yml docker-compose.pentest.yml docs/
git commit -m "docs/ops: rate-limit tiers + TRUSTED_PROXY_COUNT in prod; document edge-rate-limiting requirement"
```

---

## Interim prod hotfix (optional, before this lands)

If prod needs unblocking *today* on the current image (pre-refactor), set on the running prod env: `RATE_LIMIT_RPM=<high, e.g. 3000>` and the correct `TRUSTED_PROXY_COUNT`. `RATE_LIMIT_RPM` controls the to-be-deleted `GlobalRateLimitMiddleware`, so it takes effect immediately on the old code; after this refactor merges, that var becomes inert (`extra="ignore"`) and `RATE_LIMIT_AGGREGATE`/`TRUSTED_PROXY_COUNT` take over.

## Final verification

- [ ] `cd service && uv run pytest -q` — full suite green.
- [ ] `make lint` — ruff clean.
- [ ] `rg -n 'GlobalRateLimitMiddleware|_get_redis|_FALLBACK_LIMIT|rate_limit_rpm|RATE_LIMIT_RPM' service/src` — no hits (all removed from source; compose/docs updated).
- [ ] `uv run mkdocs build --strict` — docs build clean.
