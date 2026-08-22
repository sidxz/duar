# AuthZ-mode auto-resolve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Project hard rule (overrides the sub-skill's per-task review):** per-task *tests* are fine; there is **no per-task or per-batch review gate**. One whole-of-branch review happens after Task 7, never earlier.

**Goal:** Let scripts/Postman/Swagger call an AuthZ-mode app API with only the IdP token plus `X-Workspace-Id`; the app's SDK middleware mints (and caches) the Duar authz token itself.

**Architecture:** Opt-in flag on both dual-token middlewares (Python `AuthzMiddleware`, Next.js `createDuarAuthzMiddleware`). When `X-Authz-Token` is absent, the middleware verifies the IdP token locally as today, then calls Duar `POST /authz/resolve` with the service key for the workspace named in `X-Workspace-Id`, caches the result per `(idp_sub, workspace_id)` at 80% of its TTL with single-flight de-duplication, and continues into the unchanged authz-token validation. Zero changes to the Duar service.

**Tech Stack:** Python 3.12 / Starlette `BaseHTTPMiddleware` / httpx / PyJWT / pytest (+pytest-asyncio auto mode, respx); TypeScript / Next.js Edge middleware / jose / vitest.

**Spec:** `docs/superpowers/specs/2026-08-22-authz-auto-resolve-design.md` — read it first; every string, status code and rule below is copied from it.

## Global Constraints

- Branch: `authz-auto-resolve` (already created; spec committed as `6108c34`). Commit prefixes: `feat(sdk):`, `feat(nextjs):`, `docs:`.
- **Default off.** `auto_resolve=False` / `autoResolve: false` must leave every existing code path and test byte-identical. Baselines: `cd sdk && uv run pytest -q` → **138 passed**; `cd sdks/nextjs && npm test` → **19 passed**.
- Headers, exact: request `X-Workspace-Id` (UUID), `X-Authz-Token`, `Authorization: Bearer <idp>`. `X-Authz-Token` wins when both are present; `X-Workspace-Id` is then ignored.
- Response strings, verbatim: `Missing authz token — send X-Authz-Token, or X-Workspace-Id to resolve one server-side` (401) · `Invalid X-Workspace-Id` (400) · `IdP token rejected by Duar` (401) · `Not authorized for this workspace` (403) · `Authorization service rate limit` (429, `Retry-After` passed through when present) · `Authorization service unavailable` (503).
- Duar status → app status: 400→401 · 403→403 · 409→403 · 429→429 · anything else / network error →503.
- Cache: key `` `${idp_sub}|${workspace_id}` `` → `(authz_token, expires_at)`; expiry = **0.8 × `expires_in`** (seconds; default 300 if absent); max **4096** entries, oldest evicted; failures never cached; single-flight per key.
- Provider is explicit config: Python `idp_provider`, Next.js `idpProvider` (`"google"` | `"entra_id"`). Never inferred.
- Files outside scope — do not touch: anything under `service/`, `sdks/js/`, `sdks/react/`, `admin/`.
- Lint: `make lint` (ruff, line-length 120, rules E/F/I/UP, covers `sdk/src` + `sdk/tests`). Run `make fmt` before each Python commit.
- Docs gate: `uv run --extra docs mkdocs build --strict` from repo root.

---

## File map

| File | Change |
|---|---|
| `sdk/src/duar_auth/types.py` | `DuarError.retry_after` (new optional kwarg) |
| `sdk/src/duar_auth/authz.py` | `AuthzClient.resolve` populates `retry_after` on non-200 |
| `sdk/src/duar_auth/authz_middleware.py` | `idp_provider` / `auto_resolve` params + guards; `_auto_resolve`, `_mint`, `_mint_error`; cache + pending dicts; dispatch branch |
| `sdk/src/duar_auth/duar.py` | `Duar(idp_provider=, auto_resolve=)` + validation + `protect()` pass-through + docstring |
| `sdk/tests/test_authz_client.py` | 1 test (retry_after) |
| `sdk/tests/test_authz_middleware.py` | `_FakeAuthzClient`, `_FakeAutoDuar`, `_mint_authz`, `_make_auto_app`, `TestAutoResolve` |
| `sdk/tests/test_duar_auto_resolve.py` | new: `Duar` validation + `protect()` pass-through |
| `sdks/nextjs/src/authz-middleware.ts` | config fields + guard; `ResolveError`, closure cache/pending, `mint`/`resolveOnce`/`resolveErrorResponse`; auto branch; `x-authz-token` forwarding; `handleUnauthenticated(detail)` |
| `sdks/nextjs/src/__tests__/authz-middleware.test.ts` | new |
| `docs/sdk/middleware.md`, `docs/sdk/duar-class.md`, `docs/js-sdk/nextjs.md`, `docs/guide/how-it-works.md`, `CHANGELOG.md` | docs |

---

### Task 1: `DuarError.retry_after` and `AuthzClient.resolve` populating it

**Files:**
- Modify: `sdk/src/duar_auth/types.py:102-107`
- Modify: `sdk/src/duar_auth/authz.py:65-69`
- Test: `sdk/tests/test_authz_client.py`

**Interfaces:**
- Produces: `DuarError(message: str, status_code: int | None = None, retry_after: str | None = None)` with attributes `.status_code`, `.retry_after`. Task 3 reads both.

- [ ] **Step 1: Write the failing test**

Append to the `TestAuthzClient` class in `sdk/tests/test_authz_client.py` (and add `from duar_auth.types import DuarError` to the imports at the top):

```python
    @pytest.mark.asyncio
    async def test_error_carries_status_and_retry_after(self):
        with respx.mock:
            respx.post("http://duar:9003/authz/resolve").mock(
                return_value=Response(429, json={"detail": "slow down"}, headers={"Retry-After": "17"})
            )
            async with AuthzClient("http://duar:9003", service_key="sk_test") as client:
                with pytest.raises(DuarError) as exc_info:
                    await client.resolve(idp_token="t", provider="google", workspace_id=uuid.uuid4())
        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after == "17"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sdk && uv run pytest tests/test_authz_client.py -q`
Expected: FAIL — `AttributeError: 'DuarError' object has no attribute 'retry_after'`

- [ ] **Step 3: Implement**

`sdk/src/duar_auth/types.py` — replace the `DuarError` class:

```python
class DuarError(Exception):
    """Raised when the Duar identity service returns an error or is unreachable."""

    def __init__(self, message: str, status_code: int | None = None, retry_after: str | None = None):
        self.status_code = status_code
        self.retry_after = retry_after  # Duar's Retry-After header on 429, else None
        super().__init__(message)
```

`sdk/src/duar_auth/authz.py` — in `resolve()`, replace the `if resp.status_code != 200:` block:

```python
        if resp.status_code != 200:
            raise DuarError(
                f"Duar API error: {resp.status_code}",
                resp.status_code,
                retry_after=resp.headers.get("Retry-After"),
            )
```

- [ ] **Step 4: Run tests**

Run: `cd sdk && uv run pytest tests/test_authz_client.py tests/test_types.py -q`
Expected: all PASS (3 in test_authz_client)

- [ ] **Step 5: Commit**

```bash
make fmt
git add sdk/src/duar_auth/types.py sdk/src/duar_auth/authz.py sdk/tests/test_authz_client.py
git commit -m "feat(sdk): DuarError.retry_after, populated by AuthzClient.resolve"
```

---

### Task 2: Python `AuthzMiddleware` auto-resolve — contract, mint, cache, single-flight

**Files:**
- Modify: `sdk/src/duar_auth/authz_middleware.py` (imports, constructor, `dispatch`, two new methods)
- Test: `sdk/tests/test_authz_middleware.py` (append at end of file)

**Interfaces:**
- Consumes: `DuarError` from Task 1; `duar_instance.authz.resolve(idp_token, provider, workspace_id)` → `dict` with `authz_token: str`, `expires_in: int` (existing `AuthzClient`).
- Produces: `AuthzMiddleware(..., idp_provider: str | None = None, auto_resolve: bool = False)`; `request.state.token` = minted authz token on the auto path. Task 3 replaces the error branch; Task 4 passes the two kwargs from `Duar.protect()`.

- [ ] **Step 1: Write the failing tests**

Append to the END of `sdk/tests/test_authz_middleware.py` (after `TestAuthzKidPath`). Add `import asyncio` and `import httpx` to the top-of-file imports (keep them alphabetised: `asyncio`, `datetime`, `uuid`, then `httpx`, `jwt as pyjwt`, …).

```python
# ---------------------------------------------------------------------------
# auto_resolve: no X-Authz-Token + X-Workspace-Id → middleware mints via Duar
# ---------------------------------------------------------------------------

AUTO_WS = str(uuid.uuid4())


def _mint_authz(duar_priv, workspace_id: str, kid: str = "s1") -> str:
    """What Duar's /authz/resolve would sign for this workspace."""
    now = datetime.datetime.now(datetime.UTC)
    return pyjwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "idp_sub": "google|12345",
            "svc": TEST_SERVICE_NAME,
            "wid": str(workspace_id),
            "wslug": "acme",
            "wrole": "editor",
            "actions": ["read"],
            "aud": "duar:authz",
            "iat": now,
            "exp": now + datetime.timedelta(minutes=5),
        },
        duar_priv,
        algorithm="RS256",
        headers={"kid": kid},
    )


class _FakeAuthzClient:
    """Stand-in for AuthzClient: records resolve() calls, returns a signed mint or raises."""

    def __init__(self, duar_priv, *, fail: Exception | None = None, delay: float = 0.0):
        self.calls: list[tuple[str, str, str]] = []
        self._duar_priv = duar_priv
        self._fail = fail
        self._delay = delay

    async def resolve(self, idp_token, provider, workspace_id=None, nonce=None):
        self.calls.append((idp_token, provider, str(workspace_id)))
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._fail is not None:
            raise self._fail
        return {"authz_token": _mint_authz(self._duar_priv, str(workspace_id)), "expires_in": 300}


class _FakeAutoDuar(_FakeDuar):
    """_FakeDuar plus the ``authz`` client the auto path mints through."""

    def __init__(self, authz):
        super().__init__()
        self.authz = authz


def _make_auto_app(idp_pub, fake_duar, *, auto_resolve: bool = True) -> Starlette:
    async def protected(request: Request) -> JSONResponse:
        return JSONResponse({"email": request.state.user.email, "token": request.state.token})

    app = Starlette(routes=[Route("/protected", protected)])
    app.add_middleware(
        AuthzMiddleware,
        service_name=TEST_SERVICE_NAME,
        idp_audience=TEST_IDP_AUDIENCE,
        idp_public_key=idp_pub,
        duar_instance=fake_duar,
        idp_provider="google",
        auto_resolve=auto_resolve,
    )
    return app


@pytest.fixture()
def auto_env(idp_keypair, duar_keypair, monkeypatch):
    """JWKS served for kid s1, a verified IdP token, and a recording fake authz client."""
    idp_priv, idp_pub = idp_keypair
    duar_priv, duar_pub = duar_keypair
    _patch_jwks(monkeypatch, _jwks_for(duar_pub, "s1"))
    idp_token, authz_token = _signed_dual(idp_priv, duar_priv, "s1")
    return idp_pub, duar_priv, idp_token, authz_token


class TestAutoResolve:
    def test_missing_both_tokens_401_with_hint(self, auto_env):
        idp_pub, duar_priv, idp_token, _ = auto_env
        authz = _FakeAuthzClient(duar_priv)
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(authz)))
        resp = client.get("/protected", headers={"Authorization": f"Bearer {idp_token}"})
        assert resp.status_code == 401
        assert "X-Workspace-Id" in resp.json()["detail"]
        assert authz.calls == []

    def test_workspace_header_mints_and_sets_state(self, auto_env):
        idp_pub, duar_priv, idp_token, _ = auto_env
        authz = _FakeAuthzClient(duar_priv)
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(authz)))
        resp = client.get(
            "/protected",
            headers={"Authorization": f"Bearer {idp_token}", "X-Workspace-Id": AUTO_WS},
        )
        assert resp.status_code == 200
        assert resp.json()["email"] == "alice@acme.com"
        assert authz.calls == [(idp_token, "google", AUTO_WS)]
        minted = pyjwt.decode(resp.json()["token"], options={"verify_signature": False})
        assert minted["wid"] == AUTO_WS

    def test_second_request_hits_cache(self, auto_env):
        idp_pub, duar_priv, idp_token, _ = auto_env
        authz = _FakeAuthzClient(duar_priv)
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(authz)))
        headers = {"Authorization": f"Bearer {idp_token}", "X-Workspace-Id": AUTO_WS}
        assert client.get("/protected", headers=headers).status_code == 200
        assert client.get("/protected", headers=headers).status_code == 200
        assert len(authz.calls) == 1

    def test_cache_key_includes_workspace(self, auto_env):
        idp_pub, duar_priv, idp_token, _ = auto_env
        authz = _FakeAuthzClient(duar_priv)
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(authz)))
        other_ws = str(uuid.uuid4())
        for ws in (AUTO_WS, other_ws):
            resp = client.get(
                "/protected", headers={"Authorization": f"Bearer {idp_token}", "X-Workspace-Id": ws}
            )
            assert resp.status_code == 200
        assert [c[2] for c in authz.calls] == [AUTO_WS, other_ws]

    async def test_concurrent_misses_share_one_mint(self, auto_env):
        idp_pub, duar_priv, idp_token, _ = auto_env
        authz = _FakeAuthzClient(duar_priv, delay=0.05)
        app = _make_auto_app(idp_pub, _FakeAutoDuar(authz))
        headers = {"Authorization": f"Bearer {idp_token}", "X-Workspace-Id": AUTO_WS}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            resps = await asyncio.gather(*[c.get("/protected", headers=headers) for _ in range(5)])
        assert [r.status_code for r in resps] == [200] * 5
        assert len(authz.calls) == 1

    def test_authz_header_wins_over_workspace_header(self, auto_env):
        idp_pub, duar_priv, idp_token, authz_token = auto_env
        authz = _FakeAuthzClient(duar_priv)
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(authz)))
        resp = client.get(
            "/protected",
            headers={
                "Authorization": f"Bearer {idp_token}",
                "X-Authz-Token": authz_token,
                "X-Workspace-Id": str(uuid.uuid4()),
            },
        )
        assert resp.status_code == 200
        assert authz.calls == []

    def test_bad_workspace_uuid_400(self, auto_env):
        idp_pub, duar_priv, idp_token, _ = auto_env
        authz = _FakeAuthzClient(duar_priv)
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(authz)))
        resp = client.get(
            "/protected", headers={"Authorization": f"Bearer {idp_token}", "X-Workspace-Id": "not-a-uuid"}
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Invalid X-Workspace-Id"
        assert authz.calls == []

    def test_invalid_idp_token_never_reaches_duar(self, auto_env):
        idp_pub, duar_priv, _, _ = auto_env
        authz = _FakeAuthzClient(duar_priv)
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(authz)))
        resp = client.get("/protected", headers={"Authorization": "Bearer junk", "X-Workspace-Id": AUTO_WS})
        assert resp.status_code == 401
        assert authz.calls == []

    def test_auto_resolve_off_keeps_existing_401(self, auto_env):
        idp_pub, duar_priv, idp_token, _ = auto_env
        authz = _FakeAuthzClient(duar_priv)
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(authz), auto_resolve=False))
        resp = client.get(
            "/protected", headers={"Authorization": f"Bearer {idp_token}", "X-Workspace-Id": AUTO_WS}
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Missing authz token"
        assert authz.calls == []

    def test_requires_duar_instance(self, idp_keypair, duar_keypair):
        _, idp_pub = idp_keypair
        _, duar_pub = duar_keypair
        with pytest.raises(ValueError, match="duar_instance"):
            AuthzMiddleware(
                Starlette(),
                service_name=TEST_SERVICE_NAME,
                idp_audience=TEST_IDP_AUDIENCE,
                idp_public_key=idp_pub,
                duar_public_key=duar_pub,
                idp_provider="google",
                auto_resolve=True,
            )

    def test_requires_idp_provider(self, idp_keypair):
        _, idp_pub = idp_keypair
        with pytest.raises(ValueError, match="idp_provider"):
            AuthzMiddleware(
                Starlette(),
                service_name=TEST_SERVICE_NAME,
                idp_audience=TEST_IDP_AUDIENCE,
                idp_public_key=idp_pub,
                duar_instance=_FakeDuar(),
                auto_resolve=True,
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd sdk && uv run pytest tests/test_authz_middleware.py -q -k TestAutoResolve`
Expected: every test FAILS/ERRORS with `TypeError: AuthzMiddleware.__init__() got an unexpected keyword argument 'idp_provider'` (the guard tests too — `pytest.raises(ValueError)` does not swallow a TypeError).

- [ ] **Step 3: Implement**

`sdk/src/duar_auth/authz_middleware.py`:

(a) Imports — replace the import block at the top with:

```python
from __future__ import annotations

import asyncio
import time
import uuid
from collections import OrderedDict
from typing import TYPE_CHECKING

import httpx
import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from duar_auth.types import AuthenticatedUser, DuarError

if TYPE_CHECKING:
    from duar_auth.duar import Duar

_RESOLVE_CACHE_MAX = 4096
_MISSING_BOTH_DETAIL = "Missing authz token — send X-Authz-Token, or X-Workspace-Id to resolve one server-side"
```

(b) Class docstring — append this paragraph at the end of the `AuthzMiddleware` docstring (before the closing `"""`):

```
    **Auto-resolve (opt-in).** With ``auto_resolve=True`` a request that carries the
    IdP token and ``X-Workspace-Id`` but no ``X-Authz-Token`` has its authz token
    minted here via ``duar_instance.authz.resolve`` (service key, server-side) and
    cached per ``(idp_sub, workspace_id)`` for 80% of its TTL with single-flight
    minting. Lets scripts / Postman / Swagger call the API with one token. Requires
    ``duar_instance`` and ``idp_provider``. ``X-Authz-Token`` wins when both are sent.
```

(c) Constructor — add two keyword parameters after `exclude_paths`:

```python
        exclude_paths: list[str] | None = None,
        idp_provider: str | None = None,
        auto_resolve: bool = False,
    ):
```

and, directly after the existing `raise ValueError("AuthzMiddleware requires idp_public_key or idp_jwks_url ...")` block, add:

```python
        if auto_resolve and duar_instance is None:
            raise ValueError("AuthzMiddleware auto_resolve requires duar_instance (it mints via the service key)")
        if auto_resolve and not idp_provider:
            raise ValueError("AuthzMiddleware auto_resolve requires idp_provider (e.g. 'google', 'entra_id')")
```

and after `self.exclude_paths = ...` add:

```python
        self.idp_provider = idp_provider
        self.auto_resolve = auto_resolve
        # "idp_sub|workspace_id" -> (authz_token, monotonic expiry). Insertion-ordered for eviction.
        self._resolve_cache: OrderedDict[str, tuple[str, float]] = OrderedDict()
        # In-flight mints keyed the same way, so concurrent misses share ONE Duar call
        # (Duar's /authz/resolve bucket is 60/min per service, shared with browser mints).
        self._resolve_pending: dict[str, asyncio.Task[str]] = {}
```

(d) `dispatch` — replace step 2 (the `authz_token = request.headers.get("X-Authz-Token")` block) with:

```python
        # 2. Extract authz token. Without auto_resolve its absence is a hard 401 (unchanged).
        authz_token = request.headers.get("X-Authz-Token")
        if not authz_token and not self.auto_resolve:
            return JSONResponse(status_code=401, content={"detail": "Missing authz token"})
```

and insert between step 3 (IdP validation `try/except`) and step 4 (`# 4. Validate authz token`):

```python
        # 3b. Auto-resolve: no authz token but a target workspace — mint (or reuse) one.
        #     Runs after IdP validation so junk never consumes Duar's rate bucket.
        if not authz_token:
            resolved = await self._auto_resolve(request, idp_token, idp_payload)
            if isinstance(resolved, Response):
                return resolved
            authz_token = resolved
```

(e) New methods — add after `_decode_authz` (before `dispatch`):

```python
    async def _auto_resolve(self, request: Request, idp_token: str, idp_payload: dict) -> str | Response:
        """Return an authz token for ``X-Workspace-Id`` — cached, or minted via Duar.

        Keyed by ``(idp_sub, workspace_id)``, not the IdP token: the token was verified
        locally just before this and the ``idp_sub`` binding check still runs on the
        result, so a script fetching a fresh IdP token per request reuses the cached
        mint instead of burning one Duar call per request.
        """
        raw_wid = request.headers.get("X-Workspace-Id")
        if not raw_wid:
            return JSONResponse(status_code=401, content={"detail": _MISSING_BOTH_DETAIL})
        try:
            workspace_id = str(uuid.UUID(raw_wid))
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Invalid X-Workspace-Id"})

        key = f"{idp_payload.get('sub')}|{workspace_id}"
        cached = self._resolve_cache.get(key)
        if cached is not None and cached[1] > time.monotonic():
            return cached[0]

        task = self._resolve_pending.get(key)
        if task is None:
            task = asyncio.ensure_future(self._mint(key, idp_token, workspace_id))
            self._resolve_pending[key] = task
            task.add_done_callback(lambda _t: self._resolve_pending.pop(key, None))
        try:
            # shield: one caller disconnecting must not cancel the mint the others await.
            return await asyncio.shield(task)
        except (DuarError, httpx.HTTPError):
            return JSONResponse(status_code=503, content={"detail": "Authorization service unavailable"})

    async def _mint(self, key: str, idp_token: str, workspace_id: str) -> str:
        data = await self._duar_instance.authz.resolve(idp_token, self.idp_provider, workspace_id)
        token: str = data["authz_token"]
        ttl = 0.8 * float(data.get("expires_in") or 300)  # re-mint well before it expires mid-flight
        self._resolve_cache[key] = (token, time.monotonic() + ttl)
        self._resolve_cache.move_to_end(key)
        while len(self._resolve_cache) > _RESOLVE_CACHE_MAX:
            self._resolve_cache.popitem(last=False)
        return token
```

- [ ] **Step 4: Run the whole middleware file, then the full SDK suite**

Run: `cd sdk && uv run pytest tests/test_authz_middleware.py -q`
Expected: all PASS (existing tests untouched + 11 new).

Run: `cd sdk && uv run pytest -q`
Expected: **150 passed** (138 baseline + 1 from Task 1 + 11).

- [ ] **Step 5: Commit**

```bash
make fmt && make lint
git add sdk/src/duar_auth/authz_middleware.py sdk/tests/test_authz_middleware.py
git commit -m "feat(sdk): AuthzMiddleware auto_resolve — mint authz token from X-Workspace-Id, cached + single-flight"
```

---

### Task 3: Python mint-error mapping

**Files:**
- Modify: `sdk/src/duar_auth/authz_middleware.py` (`_auto_resolve` except-branch; new `_mint_error`)
- Test: `sdk/tests/test_authz_middleware.py` (append to `TestAutoResolve`)

**Interfaces:**
- Consumes: `DuarError.status_code`, `DuarError.retry_after` (Task 1).
- Produces: `AuthzMiddleware._mint_error(exc: DuarError) -> JSONResponse` (static).

- [ ] **Step 1: Write the failing tests**

Append inside `class TestAutoResolve` (same indentation as the other methods):

```python
    @pytest.mark.parametrize(
        ("duar_status", "expected_status", "expected_detail"),
        [
            (400, 401, "IdP token rejected by Duar"),
            (403, 403, "Not authorized for this workspace"),
            (409, 403, "Not authorized for this workspace"),
            (500, 503, "Authorization service unavailable"),
        ],
    )
    def test_duar_error_mapping(self, auto_env, duar_status, expected_status, expected_detail):
        idp_pub, duar_priv, idp_token, _ = auto_env
        authz = _FakeAuthzClient(duar_priv, fail=DuarError("Duar API error", duar_status))
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(authz)))
        resp = client.get(
            "/protected", headers={"Authorization": f"Bearer {idp_token}", "X-Workspace-Id": AUTO_WS}
        )
        assert resp.status_code == expected_status
        assert resp.json()["detail"] == expected_detail

    def test_rate_limited_passes_retry_after(self, auto_env):
        idp_pub, duar_priv, idp_token, _ = auto_env
        authz = _FakeAuthzClient(duar_priv, fail=DuarError("Duar API error", 429, retry_after="17"))
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(authz)))
        resp = client.get(
            "/protected", headers={"Authorization": f"Bearer {idp_token}", "X-Workspace-Id": AUTO_WS}
        )
        assert resp.status_code == 429
        assert resp.json()["detail"] == "Authorization service rate limit"
        assert resp.headers["Retry-After"] == "17"

    def test_network_error_503(self, auto_env):
        idp_pub, duar_priv, idp_token, _ = auto_env
        authz = _FakeAuthzClient(duar_priv, fail=httpx.ConnectError("boom"))
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(authz)))
        resp = client.get(
            "/protected", headers={"Authorization": f"Bearer {idp_token}", "X-Workspace-Id": AUTO_WS}
        )
        assert resp.status_code == 503
        assert resp.json()["detail"] == "Authorization service unavailable"

    def test_failed_mint_is_not_cached(self, auto_env):
        idp_pub, duar_priv, idp_token, _ = auto_env
        authz = _FakeAuthzClient(duar_priv, fail=DuarError("Duar API error", 500))
        client = TestClient(_make_auto_app(idp_pub, _FakeAutoDuar(authz)))
        headers = {"Authorization": f"Bearer {idp_token}", "X-Workspace-Id": AUTO_WS}
        assert client.get("/protected", headers=headers).status_code == 503
        authz._fail = None  # Duar recovers
        assert client.get("/protected", headers=headers).status_code == 200
        assert len(authz.calls) == 2
```

Add `from duar_auth.types import DuarError` to the test file's imports (first-party block, after `from duar_auth.authz_middleware import AuthzMiddleware`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd sdk && uv run pytest tests/test_authz_middleware.py -q -k "mapping or retry_after or network or not_cached"`
Expected: the 400/403/409 mapping cases and `retry_after` FAIL (everything currently maps to 503); `network_error_503` and `failed_mint_is_not_cached` PASS already.

- [ ] **Step 3: Implement**

In `_auto_resolve`, replace the `except (DuarError, httpx.HTTPError):` clause with:

```python
        except DuarError as exc:
            return self._mint_error(exc)
        except httpx.HTTPError:
            return JSONResponse(status_code=503, content={"detail": "Authorization service unavailable"})
```

Add after `_mint`:

```python
    @staticmethod
    def _mint_error(exc: DuarError) -> JSONResponse:
        """Map a Duar /authz/resolve failure to the app's response (spec §1)."""
        if exc.status_code == 400:  # IdP token rejected (aud / signature / expiry)
            return JSONResponse(status_code=401, content={"detail": "IdP token rejected by Duar"})
        if exc.status_code in (403, 409):  # not a member / org not allowed / inactive / email conflict
            return JSONResponse(status_code=403, content={"detail": "Not authorized for this workspace"})
        if exc.status_code == 429:
            headers = {"Retry-After": exc.retry_after} if exc.retry_after else None
            return JSONResponse(
                status_code=429, content={"detail": "Authorization service rate limit"}, headers=headers
            )
        return JSONResponse(status_code=503, content={"detail": "Authorization service unavailable"})
```

- [ ] **Step 4: Run tests**

Run: `cd sdk && uv run pytest -q`
Expected: **157 passed** (150 + 4 parametrized + 3).

- [ ] **Step 5: Commit**

```bash
make fmt && make lint
git add sdk/src/duar_auth/authz_middleware.py sdk/tests/test_authz_middleware.py
git commit -m "feat(sdk): map Duar mint failures to 401/403/429+Retry-After/503 on the auto-resolve path"
```

---

### Task 4: `Duar` class plumbing

**Files:**
- Modify: `sdk/src/duar_auth/duar.py:61-100` (signature, validation, attributes, docstring) and `:182-205` (`protect`)
- Create: `sdk/tests/test_duar_auto_resolve.py`

**Interfaces:**
- Consumes: `AuthzMiddleware(idp_provider=, auto_resolve=)` (Task 2).
- Produces: `Duar(..., idp_provider: str | None = None, auto_resolve: bool = False)`; attributes `duar.idp_provider`, `duar.auto_resolve`.

- [ ] **Step 1: Write the failing tests**

Create `sdk/tests/test_duar_auto_resolve.py`:

```python
"""Duar(auto_resolve=True): validation and protect() pass-through to AuthzMiddleware."""

import pytest
from starlette.applications import Starlette

from duar_auth import Duar


def _duar(public_pem: str, **kw) -> Duar:
    return Duar(
        base_url="https://duar.test",
        service_name="reports",
        service_key="svc-key",
        idp_public_key=public_pem,
        idp_audience="my-client-id",
        **kw,
    )


def test_auto_resolve_requires_idp_provider(rsa_keypair):
    _, public_pem = rsa_keypair
    with pytest.raises(ValueError, match="idp_provider"):
        _duar(public_pem, auto_resolve=True)


def test_protect_passes_auto_resolve_to_middleware(rsa_keypair):
    _, public_pem = rsa_keypair
    app = Starlette(routes=[])
    _duar(public_pem, auto_resolve=True, idp_provider="google").protect(app)
    kwargs = app.user_middleware[0].kwargs
    assert kwargs["auto_resolve"] is True
    assert kwargs["idp_provider"] == "google"


def test_auto_resolve_defaults_off(rsa_keypair):
    _, public_pem = rsa_keypair
    app = Starlette(routes=[])
    _duar(public_pem).protect(app)
    assert app.user_middleware[0].kwargs["auto_resolve"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd sdk && uv run pytest tests/test_duar_auto_resolve.py -q`
Expected: FAIL — `TypeError: Duar.__init__() got an unexpected keyword argument 'auto_resolve'` / `KeyError: 'auto_resolve'`.

- [ ] **Step 3: Implement**

`sdk/src/duar_auth/duar.py`:

(a) Docstring `Args:` — append after the `cache_ttl:` entry:

```
        idp_provider: IdP provider name Duar validates tokens as (``"google"``,
            ``"entra_id"``). Required when ``auto_resolve=True``.
        auto_resolve: Let ``AuthzMiddleware`` mint the authz token server-side when a
            request carries only the IdP token plus ``X-Workspace-Id`` (scripts,
            Postman, Swagger). Default ``False`` — existing dual-token behaviour
            unchanged. AuthZ mode only.
```

(b) Signature — after `cache_ttl: float = 0,` add:

```python
        idp_provider: str | None = None,
        auto_resolve: bool = False,
```

(c) Validation — inside the existing `if mode == "authz":` block, after the `idp_audience` check, add:

```python
            if auto_resolve and not idp_provider:
                raise ValueError("idp_provider is required when auto_resolve=True (e.g. 'google', 'entra_id')")
```

(d) Attributes — after `self.cache_ttl = cache_ttl` add:

```python
        self.idp_provider = idp_provider
        self.auto_resolve = auto_resolve
```

(e) `protect()` — in the `app.add_middleware(AuthzMiddleware, ...)` call add two kwargs after `exclude_paths=exclude_paths,`:

```python
                idp_provider=self.idp_provider,
                auto_resolve=self.auto_resolve,
```

- [ ] **Step 4: Run tests**

Run: `cd sdk && uv run pytest -q`
Expected: **160 passed**.

- [ ] **Step 5: Commit**

```bash
make fmt && make lint
git add sdk/src/duar_auth/duar.py sdk/tests/test_duar_auto_resolve.py
git commit -m "feat(sdk): Duar(idp_provider=, auto_resolve=) plumbed through protect()"
```

---

### Task 5: Next.js `createDuarAuthzMiddleware` auto-resolve

**Files:**
- Modify: `sdks/nextjs/src/authz-middleware.ts`
- Create: `sdks/nextjs/src/__tests__/authz-middleware.test.ts`

**Interfaces:**
- Consumes: `verifyToken` from `@duar-auth/js/server` (unchanged); global `fetch`.
- Produces: config fields `autoResolve?: boolean`, `serviceKey?: string`, `idpProvider?: string`; forwarded request header `x-authz-token` on the auto path.

Test mechanics follow the repo's existing convention (`sdks/js/src/__tests__/jwt-verifier.test.ts`): mock `jose` and `@duar-auth/js/server` so token *values* select canned payloads; mint calls go through a stubbed global `fetch`. `NextResponse.next({ request: { headers } })` exposes forwarded headers as `x-middleware-request-<name>` on the response — assert on those.

- [ ] **Step 1: Write the failing tests**

Create `sdks/nextjs/src/__tests__/authz-middleware.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('jose', () => ({
  createRemoteJWKSet: vi.fn(() => vi.fn()),
  jwtVerify: vi.fn(),
}))
vi.mock('@duar-auth/js/server', () => ({ verifyToken: vi.fn() }))

import { jwtVerify } from 'jose'
import { verifyToken } from '@duar-auth/js/server'
import { NextRequest } from 'next/server'
import { createDuarAuthzMiddleware } from '../authz-middleware'

const WS = '5e60ba90-4b3e-4b1a-9dcb-9d76b1a1e3a1'
const IDP_PAYLOAD = { sub: 'google|1', email: 'a@acme.com', name: 'A' }
const AUTHZ_PAYLOAD = { sub: 'u1', idp_sub: 'google|1', svc: 'my-app', wid: WS, wslug: 'acme', wrole: 'editor' }

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', ...headers },
  })
}

let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  vi.mocked(jwtVerify).mockImplementation(async (token) =>
    token === 'idp-ok' ? ({ payload: IDP_PAYLOAD } as any) : Promise.reject(new Error('bad idp')),
  )
  vi.mocked(verifyToken).mockImplementation(async (token) =>
    token === 'authz-ok' || token === 'minted' ? (AUTHZ_PAYLOAD as any) : Promise.reject(new Error('bad authz')),
  )
  fetchMock = vi.fn().mockResolvedValue(jsonResponse({ authz_token: 'minted', expires_in: 300 }))
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.mocked(jwtVerify).mockReset()
  vi.mocked(verifyToken).mockReset()
})

function mw(overrides: Record<string, unknown> = {}) {
  return createDuarAuthzMiddleware({
    duarUrl: 'http://duar:9003',
    idpJwksUrl: 'https://idp.example/jwks',
    idpAudience: 'client-id',
    serviceName: 'my-app',
    autoResolve: true,
    serviceKey: 'sk_test',
    idpProvider: 'google',
    ...overrides,
  })
}

function api(headers: Record<string, string>, path = '/api/items') {
  return new NextRequest(`https://app.example.com${path}`, { headers })
}

describe('createDuarAuthzMiddleware autoResolve', () => {
  it('throws when autoResolve is set without serviceKey or idpProvider', () => {
    expect(() => mw({ serviceKey: undefined })).toThrow(/serviceKey/)
    expect(() => mw({ idpProvider: undefined })).toThrow(/idpProvider/)
  })

  it('401 with a hint when neither X-Authz-Token nor X-Workspace-Id is sent', async () => {
    const res = await mw()(api({ authorization: 'Bearer idp-ok' }))
    expect(res.status).toBe(401)
    expect((await res.json()).detail).toContain('X-Workspace-Id')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('mints via /authz/resolve and forwards x-authz-token + x-duar-* headers', async () => {
    const res = await mw()(api({ authorization: 'Bearer idp-ok', 'x-workspace-id': WS }))
    expect(res.status).toBe(200)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('http://duar:9003/authz/resolve')
    expect(init.method).toBe('POST')
    expect(init.headers['X-Service-Key']).toBe('sk_test')
    expect(JSON.parse(init.body)).toEqual({ idp_token: 'idp-ok', provider: 'google', workspace_id: WS })
    expect(res.headers.get('x-middleware-request-x-authz-token')).toBe('minted')
    expect(res.headers.get('x-middleware-request-x-duar-user-id')).toBe('u1')
    expect(res.headers.get('x-middleware-request-x-duar-workspace-id')).toBe(WS)
  })

  it('second request for the same user+workspace is served from cache', async () => {
    const m = mw()
    await m(api({ authorization: 'Bearer idp-ok', 'x-workspace-id': WS }))
    const res = await m(api({ authorization: 'Bearer idp-ok', 'x-workspace-id': WS }))
    expect(res.status).toBe(200)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('concurrent first requests share one mint (single-flight)', async () => {
    fetchMock.mockImplementation(
      () => new Promise((r) => setTimeout(() => r(jsonResponse({ authz_token: 'minted', expires_in: 300 })), 20)),
    )
    const m = mw()
    const req = () => m(api({ authorization: 'Bearer idp-ok', 'x-workspace-id': WS }))
    const results = await Promise.all([req(), req(), req()])
    expect(results.map((r) => r.status)).toEqual([200, 200, 200])
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('X-Authz-Token wins over X-Workspace-Id', async () => {
    const res = await mw()(
      api({ authorization: 'Bearer idp-ok', 'x-authz-token': 'authz-ok', 'x-workspace-id': WS }),
    )
    expect(res.status).toBe(200)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('400 on a malformed X-Workspace-Id, before any Duar call', async () => {
    const res = await mw()(api({ authorization: 'Bearer idp-ok', 'x-workspace-id': 'nope' }))
    expect(res.status).toBe(400)
    expect((await res.json()).detail).toBe('Invalid X-Workspace-Id')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('an invalid IdP token never reaches Duar', async () => {
    const res = await mw()(api({ authorization: 'Bearer junk', 'x-workspace-id': WS }))
    expect(res.status).toBe(401)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it.each([
    [400, 401, 'IdP token rejected by Duar'],
    [403, 403, 'Not authorized for this workspace'],
    [409, 403, 'Not authorized for this workspace'],
    [500, 503, 'Authorization service unavailable'],
  ])('maps Duar %i to %i', async (duarStatus, expected, detail) => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'x' }, duarStatus))
    const res = await mw()(api({ authorization: 'Bearer idp-ok', 'x-workspace-id': WS }))
    expect(res.status).toBe(expected)
    expect((await res.json()).detail).toBe(detail)
  })

  it('429 passes Retry-After through', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'x' }, 429, { 'Retry-After': '17' }))
    const res = await mw()(api({ authorization: 'Bearer idp-ok', 'x-workspace-id': WS }))
    expect(res.status).toBe(429)
    expect(res.headers.get('retry-after')).toBe('17')
    expect((await res.json()).detail).toBe('Authorization service rate limit')
  })

  it('network failure → 503 and is not cached', async () => {
    fetchMock.mockRejectedValueOnce(new Error('ECONNREFUSED'))
    const m = mw()
    const first = await m(api({ authorization: 'Bearer idp-ok', 'x-workspace-id': WS }))
    expect(first.status).toBe(503)
    const second = await m(api({ authorization: 'Bearer idp-ok', 'x-workspace-id': WS }))
    expect(second.status).toBe(200)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('autoResolve off keeps the existing 401 even with X-Workspace-Id', async () => {
    const res = await mw({ autoResolve: false, serviceKey: undefined, idpProvider: undefined })(
      api({ authorization: 'Bearer idp-ok', 'x-workspace-id': WS }),
    )
    expect(res.status).toBe(401)
    expect((await res.json()).detail).toBe('Unauthorized')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('page routes without an authz token still redirect to loginPath', async () => {
    const res = await mw()(api({ authorization: 'Bearer idp-ok' }, '/dashboard'))
    expect(res.status).toBe(307)
    expect(res.headers.get('location')).toBe('https://app.example.com/login')
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd sdks/nextjs && npm test`
Expected: the new file fails — the guard test ("DID NOT THROW"), the mint/cache/single-flight/mapping tests get 401 `Unauthorized` instead of the auto-path responses. Existing 19 still pass.

- [ ] **Step 3: Implement**

`sdks/nextjs/src/authz-middleware.ts` — apply these edits:

(a) Imports — change the first line to `import { createRemoteJWKSet, jwtVerify, type JWTPayload } from 'jose'`.

(b) Config interface — add after `issuer?: string`:

```ts
  /**
   * Mint the authz token server-side when a request carries only the IdP token plus
   * `X-Workspace-Id` (scripts, Postman, Swagger). Cached per (idp_sub, workspace) at
   * 80% of the token TTL with single-flight minting. Requires `serviceKey` and
   * `idpProvider`. Default false — existing behaviour unchanged.
   */
  autoResolve?: boolean
  /** Service key for `POST /authz/resolve`. Server-only env (never NEXT_PUBLIC_). Required with autoResolve. */
  serviceKey?: string
  /** Provider Duar validates the IdP token as: 'google' | 'entra_id'. Required with autoResolve. */
  idpProvider?: string
```

(c) Module-level helpers — add after the `getJWKS` function:

```ts
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const RESOLVE_CACHE_MAX = 4096
const MISSING_BOTH_DETAIL =
  'Missing authz token — send X-Authz-Token, or X-Workspace-Id to resolve one server-side'

/** A failed `POST /authz/resolve`; `status` 0 means the request itself failed. */
class ResolveError extends Error {
  constructor(
    readonly status: number,
    readonly retryAfter: string | null,
  ) {
    super(`authz resolve failed: ${status}`)
  }
}

/** Map a Duar mint failure to the app's response (spec §1). */
function resolveErrorResponse(e: ResolveError): NextResponse {
  if (e.status === 400) {
    return NextResponse.json({ detail: 'IdP token rejected by Duar' }, { status: 401 })
  }
  if (e.status === 403 || e.status === 409) {
    return NextResponse.json({ detail: 'Not authorized for this workspace' }, { status: 403 })
  }
  if (e.status === 429) {
    const headers: Record<string, string> = e.retryAfter ? { 'Retry-After': e.retryAfter } : {}
    return NextResponse.json({ detail: 'Authorization service rate limit' }, { status: 429, headers })
  }
  return NextResponse.json({ detail: 'Authorization service unavailable' }, { status: 503 })
}
```

(d) Factory — destructure the new fields and guard. Replace the destructuring + the two existing `throw` checks at the top of `createDuarAuthzMiddleware` with:

```ts
  const {
    duarUrl,
    idpJwksUrl,
    idpAudience,
    idpIssuer,
    serviceName,
    effectiveScope,
    publicPaths = [],
    loginPath = '/login',
    autoResolve = false,
    serviceKey,
    idpProvider,
  } = config

  if (!serviceName) {
    throw new Error('createDuarAuthzMiddleware: serviceName is required')
  }
  if (!idpAudience || (Array.isArray(idpAudience) && idpAudience.length === 0)) {
    throw new Error('createDuarAuthzMiddleware: idpAudience is required')
  }
  if (autoResolve && !serviceKey) {
    throw new Error('createDuarAuthzMiddleware: autoResolve requires serviceKey')
  }
  if (autoResolve && !idpProvider) {
    throw new Error('createDuarAuthzMiddleware: autoResolve requires idpProvider')
  }

  const duarBase = duarUrl.replace(/\/+$/, '')
  const duarJwksUrl = `${duarBase}/.well-known/jwks.json`
  const issuer = config.issuer ?? duarBase

  // Auto-resolve state lives in this closure (not module scope) so each middleware
  // instance — and each test — gets its own cache.
  const resolveCache = new Map<string, { token: string; expiresAt: number }>()
  const resolvePending = new Map<string, Promise<string>>()

  async function mint(key: string, idpToken: string, workspaceId: string): Promise<string> {
    let res: Response
    try {
      res = await fetch(`${duarBase}/authz/resolve`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'X-Service-Key': serviceKey! },
        body: JSON.stringify({ idp_token: idpToken, provider: idpProvider, workspace_id: workspaceId }),
      })
    } catch {
      throw new ResolveError(0, null)
    }
    if (!res.ok) throw new ResolveError(res.status, res.headers.get('retry-after'))
    const data = (await res.json()) as { authz_token: string; expires_in?: number }
    // 80% of the TTL (same rule as M2mTokenClient) so a cached token never goes out seconds before expiry.
    resolveCache.set(key, { token: data.authz_token, expiresAt: Date.now() + 0.8 * (data.expires_in ?? 300) * 1000 })
    while (resolveCache.size > RESOLVE_CACHE_MAX) {
      const oldest = resolveCache.keys().next().value
      if (oldest === undefined) break
      resolveCache.delete(oldest)
    }
    return data.authz_token
  }

  /** Cached token for (idp_sub, workspace), else one shared in-flight mint per key. */
  function resolveOnce(key: string, idpToken: string, workspaceId: string): Promise<string> {
    const cached = resolveCache.get(key)
    if (cached && cached.expiresAt > Date.now()) return Promise.resolve(cached.token)
    let pending = resolvePending.get(key)
    if (!pending) {
      pending = mint(key, idpToken, workspaceId).finally(() => resolvePending.delete(key))
      resolvePending.set(key, pending)
    }
    return pending
  }
```

(Delete the old `const duarJwksUrl = ...` and `const issuer = ...` lines that this block replaces.)

(e) Request handling — replace everything from `// Extract IdP token from Authorization header` through the end of the `try { ... } catch { ... }` with:

```ts
    // Extract IdP token from Authorization header
    const authHeader = req.headers.get('authorization')
    const idpToken = authHeader?.startsWith('Bearer ')
      ? authHeader.slice(7)
      : null

    // Extract authz token from X-Authz-Token header. Without autoResolve its absence
    // is a hard 401 (unchanged).
    let authzToken = req.headers.get('x-authz-token')

    if (!idpToken || (!authzToken && !autoResolve)) {
      return handleUnauthenticated(req, loginPath)
    }

    try {
      const idpVerifyOptions: {
        audience: string | string[]
        issuer?: string
      } = { audience: idpAudience }
      if (idpIssuer) idpVerifyOptions.issuer = idpIssuer

      let idpPayload: JWTPayload
      let authzPayload: Awaited<ReturnType<typeof verifyToken>>

      if (authzToken) {
        // Verify both tokens in parallel.
        // IdP token: signature + audience (+ optional issuer).
        // Authz token: signature + audience via Duar's verifyToken.
        const [idpResult, verified] = await Promise.all([
          jwtVerify(idpToken, getJWKS(idpJwksUrl), idpVerifyOptions),
          verifyToken(authzToken, { jwksUrl: duarJwksUrl, audience: 'duar:authz', issuer }),
        ])
        idpPayload = idpResult.payload
        authzPayload = verified
      } else {
        // Auto-resolve: verify the IdP token FIRST so junk never reaches Duar's rate
        // bucket, then mint (or reuse) an authz token for X-Workspace-Id.
        idpPayload = (await jwtVerify(idpToken, getJWKS(idpJwksUrl), idpVerifyOptions)).payload
        const workspaceId = req.headers.get('x-workspace-id')
        if (!workspaceId) {
          return handleUnauthenticated(req, loginPath, MISSING_BOTH_DETAIL)
        }
        if (!UUID_RE.test(workspaceId)) {
          return NextResponse.json({ detail: 'Invalid X-Workspace-Id' }, { status: 400 })
        }
        try {
          authzToken = await resolveOnce(`${idpPayload.sub}|${workspaceId.toLowerCase()}`, idpToken, workspaceId)
        } catch (e) {
          if (e instanceof ResolveError) return resolveErrorResponse(e)
          throw e
        }
        authzPayload = await verifyToken(authzToken, { jwksUrl: duarJwksUrl, audience: 'duar:authz', issuer })
        // Server code that reads the raw header keeps working on the auto path.
        requestHeaders.set('x-authz-token', authzToken)
      }

      // Check idp_sub binding: authz token's idp_sub must match IdP token's sub.
      const authzClaims = authzPayload as unknown as Record<string, unknown>
      if (!idpPayload.sub || !authzClaims.idp_sub || authzClaims.idp_sub !== idpPayload.sub) {
        return handleUnauthenticated(req, loginPath)
      }

      // Enforce svc binding: the authz token was minted for this service's shared
      // scope — its own name (standalone) or its realm slug (effectiveScope).
      const allowedSvc = new Set([serviceName, effectiveScope].filter(Boolean))
      if (!authzClaims.svc || !allowedSvc.has(authzClaims.svc as string)) {
        return handleUnauthenticated(req, loginPath)
      }

      // Forward verified user info in request headers for server components / route handlers
      // Identity (email, name) comes from IdP token; authorization from authz token
      requestHeaders.set('x-duar-user-id', String(authzPayload.sub))
      // Email/name may contain code points >255 (ByteString limit) — encode.
      requestHeaders.set(
        'x-duar-email',
        encodeHeaderValue(String(idpPayload.email ?? '')),
      )
      requestHeaders.set(
        'x-duar-name',
        encodeHeaderValue(String(idpPayload.name ?? '')),
      )
      requestHeaders.set('x-duar-workspace-id', String(authzPayload.wid))
      requestHeaders.set('x-duar-workspace-slug', String(authzPayload.wslug))
      requestHeaders.set('x-duar-workspace-role', String(authzPayload.wrole))
      requestHeaders.set('x-duar-idp-sub', String(authzClaims.idp_sub))

      return NextResponse.next({ request: { headers: requestHeaders } })
    } catch {
      return handleUnauthenticated(req, loginPath)
    }
```

(f) `handleUnauthenticated` — add an optional detail:

```ts
function handleUnauthenticated(
  req: NextRequest,
  loginPath: string,
  detail = 'Unauthorized',
): NextResponse {
  const isApiRoute = req.nextUrl.pathname.startsWith('/api/')
  if (isApiRoute) {
    return NextResponse.json({ detail }, { status: 401 })
  }
  const loginUrl = req.nextUrl.clone()
  loginUrl.pathname = loginPath
  return NextResponse.redirect(loginUrl)
}
```

(g) Update the usage docblock above the factory — add these three lines to the example config after `serviceName: 'my-app',`:

```ts
 *   // Optional: let scripts call the API with only the IdP token + X-Workspace-Id
 *   autoResolve: true, serviceKey: process.env.DUAR_SERVICE_KEY!, idpProvider: 'google',
```

- [ ] **Step 4: Run tests + build**

Run: `cd sdks/nextjs && npm test`
Expected: **35 passed** (19 baseline + 16 new: 12 `it` + 4 `it.each` rows).

Run: `cd sdks/nextjs && npm run build`
Expected: tsup succeeds with no type errors.

- [ ] **Step 5: Commit**

```bash
git add sdks/nextjs/src/authz-middleware.ts sdks/nextjs/src/__tests__/authz-middleware.test.ts
git commit -m "feat(nextjs): createDuarAuthzMiddleware autoResolve — mint from X-Workspace-Id, cached + single-flight, mapped errors"
```

---

### Task 6: Docs + changelog

**Files:**
- Modify: `docs/sdk/middleware.md` (Headers table, Constructor Parameters, new section after "Validation Steps", Error Responses)
- Modify: `docs/sdk/duar-class.md` (Constructor Parameters, AuthZ Mode section)
- Modify: `docs/js-sdk/nextjs.md` (options table, "Headers set by middleware", new section)
- Modify: `docs/guide/how-it-works.md` (one paragraph)
- Modify: `CHANGELOG.md` (`[Unreleased]`)

- [ ] **Step 1: `docs/sdk/middleware.md`**

Headers table — add a row:

```markdown
| `X-Workspace-Id` | `<workspace uuid>` — only with `auto_resolve=True`, instead of `X-Authz-Token` |
```

Constructor Parameters table — add two rows before `exclude_paths`:

```markdown
| `idp_provider` | `str \| None` | `None` | Provider Duar validates IdP tokens as: `"google"` or `"entra_id"`. Required with `auto_resolve`. |
| `auto_resolve` | `bool` | `False` | Mint the authz token server-side when a request carries only the IdP token plus `X-Workspace-Id`. Requires `duar_instance` and `idp_provider`. |
```

After the "Validation Steps" list (before the `---` that precedes `## JWTAuthMiddleware`), insert:

````markdown
### Calling the API from scripts (auto-resolve)

Browsers hold both tokens; a script, Postman, or Swagger usually has only an IdP token. With `auto_resolve=True` the middleware mints the authz token itself when a request sends the IdP token plus the target workspace:

```python
duar = Duar(..., idp_provider="google", auto_resolve=True)
duar.protect(app)
```

```bash
curl https://api.example.com/reports \
  -H "Authorization: Bearer $ID_TOKEN" \
  -H "X-Workspace-Id: 5e60ba90-4b3e-4b1a-9dcb-9d76b1a1e3a1"
```

How it works: the IdP token is verified locally first (bad tokens never reach Duar), then the middleware calls `POST /authz/resolve` with the service key, caches the token per `(idp_sub, workspace)` for 80% of its TTL, and de-duplicates concurrent first requests into one mint. Everything after that — `idp_sub` binding, `svc` binding, `request.state` — is the normal dual-token path. `X-Authz-Token` always wins when both headers are present. Missing both → `401` telling the caller which header to send; a non-UUID `X-Workspace-Id` → `400`.

Not a new trust boundary: the app could already mint for any valid IdP token through its mint endpoint; this only removes the second round trip. Membership, organisation, and active-user checks still run on every mint, and the IdP token's `aud` is still pinned to your client id. Opaque (GitHub) tokens are not supported — the middleware requires a JWT IdP token.
````

Error Responses table — add rows:

```markdown
| 401 | `Missing authz token — send X-Authz-Token, or X-Workspace-Id to resolve one server-side` | `auto_resolve=True` and neither header sent |
| 400 | `Invalid X-Workspace-Id` | `X-Workspace-Id` is not a UUID (auto-resolve) |
| 401 | `IdP token rejected by Duar` | Duar refused the IdP token at mint (auto-resolve) |
| 403 | `Not authorized for this workspace` | Not a member / org not allowed / inactive (auto-resolve) |
| 429 | `Authorization service rate limit` | Duar's `/authz/resolve` limit hit; `Retry-After` passed through (auto-resolve) |
| 503 | `Authorization service unavailable` | Duar unreachable or 5xx at mint (auto-resolve) |
```

- [ ] **Step 2: `docs/sdk/duar-class.md`**

Constructor Parameters table — add after `cache_ttl`:

```markdown
| `idp_provider` | `str \| None` | `None` | Provider Duar validates IdP tokens as: `"google"` or `"entra_id"`. Required when `auto_resolve=True`. |
| `auto_resolve` | `bool` | `False` | AuthZ mode: mint the authz token server-side when a request carries only the IdP token plus `X-Workspace-Id` (scripts, Postman, Swagger). See [Middleware → auto-resolve](middleware.md#calling-the-api-from-scripts-auto-resolve). |
```

AuthZ Mode section — after the bullet list ending `- `authz_token.svc == service_name` (prevents cross-service token replay)`, add:

```markdown
**Scripts and API clients.** Pass `idp_provider="google"` (or `"entra_id"`) and `auto_resolve=True` to accept `Authorization: Bearer <idp_token>` + `X-Workspace-Id: <uuid>` with no `X-Authz-Token`; the middleware mints and caches the authz token for you. Details and the error contract: [Middleware → auto-resolve](middleware.md#calling-the-api-from-scripts-auto-resolve).
```

- [ ] **Step 3: `docs/js-sdk/nextjs.md`**

Options table — add after `loginPath`:

```markdown
| `autoResolve` | `boolean` | `false` | Mint the authz token server-side when a request carries only the IdP token plus `X-Workspace-Id` (scripts, Postman, Swagger). Requires `serviceKey` and `idpProvider`. |
| `serviceKey` | `string` | `undefined` | Service key used for `POST /authz/resolve`. Server-only env — never `NEXT_PUBLIC_`. |
| `idpProvider` | `string` | `undefined` | Provider Duar validates the IdP token as: `'google'` or `'entra_id'`. |
```

After the "What it does: …" paragraph, insert:

````markdown
### Calling the API from scripts (auto-resolve)

```ts
export default createDuarAuthzMiddleware({
  ...,
  autoResolve: true,
  serviceKey: process.env.DUAR_SERVICE_KEY!,
  idpProvider: 'google',
})
```

```bash
curl https://app.example.com/api/items \
  -H "Authorization: Bearer $ID_TOKEN" \
  -H "X-Workspace-Id: 5e60ba90-4b3e-4b1a-9dcb-9d76b1a1e3a1"
```

The IdP token is verified first, then the middleware mints through `POST /authz/resolve`, caches per `(idp_sub, workspace)` for 80% of the token TTL, de-duplicates concurrent first requests, and forwards the minted token as `x-authz-token` to your route handlers. `X-Authz-Token` wins when both headers are sent. Responses on `/api/*`: missing both headers → `401` (detail names the headers); non-UUID workspace → `400`; Duar rejects the IdP token → `401`; not a member → `403`; Duar rate limit → `429` with `Retry-After`; Duar unreachable → `503`. Page routes still redirect to `loginPath`.
````

"Headers set by middleware" table — add a row:

```markdown
| `x-authz-token` | The Duar authz token — on the auto-resolve path the middleware sets it so route handlers see the minted token |
```

- [ ] **Step 4: `docs/guide/how-it-works.md`**

After the paragraph that begins `The authz token also carries an `svc` claim …`, add:

```markdown
**Scripts and API clients.** A script rarely holds a Duar authz token. With auto-resolve enabled in the SDK middleware (`auto_resolve=True` / `autoResolve: true`), a request carrying only the IdP token plus `X-Workspace-Id` has its authz token minted server-side — the same `POST /authz/resolve` call, made by the backend with its service key, cached per user and workspace. One token for scripts, Postman, and Swagger; the browser flow is unchanged.
```

- [ ] **Step 5: `CHANGELOG.md`**

Replace the `[Unreleased]` block:

```markdown
## [Unreleased]

### Added
- AuthZ mode auto-resolve: the SDK middleware can mint the authz token server-side when a request carries only the IdP token plus `X-Workspace-Id` — scripts, Postman, and Swagger call app APIs with one token. Opt-in (`Duar(idp_provider=..., auto_resolve=True)` / `createDuarAuthzMiddleware({ autoResolve, serviceKey, idpProvider })`), default off. Cached per `(idp_sub, workspace)` at 80% of the token TTL with single-flight minting; Duar mint failures map to 401/403/429 (+`Retry-After`)/503. No service changes.
- `duar_auth.DuarError.retry_after` — Duar's `Retry-After` header on 429 responses.
```

- [ ] **Step 6: Build docs strictly**

Run: `uv run --extra docs mkdocs build --strict`
Expected: build succeeds (no warnings — the anchor `#calling-the-api-from-scripts-auto-resolve` must resolve; MkDocs slugifies the heading "Calling the API from scripts (auto-resolve)" to exactly that).

- [ ] **Step 7: Commit**

```bash
git add docs/sdk/middleware.md docs/sdk/duar-class.md docs/js-sdk/nextjs.md docs/guide/how-it-works.md CHANGELOG.md
git commit -m "docs: AuthZ-mode auto-resolve (X-Workspace-Id) for Python + Next.js SDKs; changelog"
```

---

### Task 7: Whole-branch verification

**Files:** none modified.

- [ ] **Step 1: Full gates**

```bash
make lint
cd sdk && uv run pytest -q                     # expected: 160 passed
cd sdks/nextjs && npm test && npm run build    # expected: 35 passed; build clean
cd ../.. && uv run --extra docs mkdocs build --strict
git status --short                             # expected: clean
```

- [ ] **Step 2: Default-off proof**

`git diff main -- sdk/tests/test_authz_middleware.py | grep '^-' | grep -v '^---'` must print nothing — no existing test line was changed or removed.

- [ ] **Step 3: Hand off for the single whole-of-branch review**

Per the project hard rule, this is the ONE review point. Summarise for the reviewer: the 6 commits on `authz-auto-resolve`, the spec path, and the three gate outputs above.
