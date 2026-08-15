# Cross-SDK Realm Integration Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add integration tests that exercise the **real** Duar token minters against the **real** SDK verifiers — Python and JS — proving realm Flow A (shared user-context) and Flow B (no-user m2m) hold end-to-end across a Python app and a JS app in one realm.

**Architecture:** A standalone `gen_fixtures.py` mints a committed `fixtures.json` (JWKS + labelled tokens) using the service's real `create_authz_token`/`create_m2m_token` and an ephemeral keypair. A Python in-process test (`service/tests/integration/`) mints live through the real `/realm/m2m-token` endpoint + real `create_authz_token`, then verifies with the real `Duar.verify_m2m_token` and real `AuthzMiddleware` (no ports, no DB/Redis — TestClient + `dependency_overrides`). A JS test (`sdks/js`) un-mocks `jose` and verifies the same committed token bytes.

**Tech Stack:** Python 3.12, pytest, FastAPI `TestClient`, PyJWT, `duar_auth` SDK. JS: vitest, real `jose`. Both packages live in one uv workspace sharing a venv.

## Global Constraints

- **Touch ONLY** new test/fixture files, `gen_fixtures.py`, the new JS test, and `Makefile`. **NEVER** modify, stage, format, or discard `service/src/services/role_service.py` or `service/tests/test_register_actions.py` (the user's uncommitted work).
- **Stage only the files each task lists** (`git add <those paths>`); never `git add -A` / `git add .`. Commit after every task.
- **No new dependencies, no `pyproject.toml` edits.** Verified: the shared workspace venv already imports both `duar_auth` and `src.*` in the service test run (`cd service && uv run python -c "import duar_auth, src.auth.jwt"` succeeds).
- **No committed private key.** `gen_fixtures.py` uses an **ephemeral in-memory RSA keypair** (temp files, discarded). Only `fixtures.json` (public PEM + JWKS + tokens) is committed.
- **Python run/format:** tests run `cd service && uv run pytest tests/integration/`. Format **changed files only**: `cd service && uv run ruff format <files> && uv run ruff check --fix <files>`. **NEVER** `ruff format .` / whole-tree `--fix` / `make fmt` (they would reformat the user's uncommitted `role_service.py`).
- **JS run/style:** `cd sdks/js && npm test` (vitest). Match existing JS style — **2-space indent, single quotes, no semicolons**. The new test file must **NOT** `vi.mock('jose')` (real crypto is the point); per-file mocking keeps it isolated from the existing mocked `m2m.test.ts`.
- **Frozen identifiers** (from shipped code): minters `create_m2m_token(svc, caller, ttl_s, actions=None, aud_target=None)` and `create_authz_token(user_id, idp_sub, workspace_id, workspace_slug, workspace_role, actions, service_name, org_id, org_slug, org_is_public)`; audiences `sentinel:m2m` / `sentinel:authz`; SDK `Duar(base_url, service_name, service_key, idp_public_key=…, idp_audience=…)` with attrs `_duar_public_key` / `_effective_scope`, method `verify_m2m_token(token) -> SystemAuth` (raises `duar_auth.types.DuarError`, `.status_code` 401/403); `AuthzMiddleware(service_name=…, idp_audience=…, idp_public_key=…, duar_public_key=…, duar_instance=…)`, headers `Authorization: Bearer <idp>` + `X-Authz-Token: <authz>`; JS `verifyM2mToken(token, { jwksUrl, effectiveScope, serviceName?, issuer? }) -> SystemAuth` (`{caller, actions, svc, can}`).
- Branch: `realm-trusted-app-group` (already checked out).
- **SDD housekeeping (before Task 1):** archive `.superpowers/sdd/progress.md` → `progress-plan6-archive.md`; start a fresh ledger; regenerate each `task-N-brief` from THIS plan. One implementer at a time.

## File Structure

```
service/tests/integration/
  gen_fixtures.py                 # ephemeral key → real minters → fixtures/fixtures.json
  fixtures/fixtures.json          # COMMITTED: { issuer, public_pem, jwks, tokens{5} }
  test_fixtures.py                # Task 1: decode + structural correctness of fixtures.json
  test_realm_flow_b.py            # Task 2: live m2m mint → SDK verify + negatives + freshness guard
  test_realm_flow_a.py            # Task 3: real authz mint → AuthzMiddleware accept/reject
sdks/js/src/__tests__/
  realm-integration.test.ts       # Task 4: jose UN-mocked; verifies the committed bytes
Makefile                          # Task 1: `realm-fixtures` target
```

Test basenames are unique across the `tests/` tree, so no `tests/integration/__init__.py` is needed (matches the flat `tests/` layout; `pythonpath=["."]` + unique basenames).

---

## Task 1: Fixture generator + committed `fixtures.json`

**Files:**
- Create: `service/tests/integration/gen_fixtures.py`
- Create: `service/tests/integration/fixtures/fixtures.json` (generated, committed)
- Create: `service/tests/integration/test_fixtures.py`
- Modify: `Makefile` (add `realm-fixtures` target)

**Interfaces:**
- Consumes: real `src.auth.jwt.create_authz_token`, `create_m2m_token`, `src.auth.jwks.build_jwks`, `src.auth.key_provider`.
- Produces: `fixtures.json` with keys `issuer` (str), `public_pem` (str), `jwks` (`{"keys":[…]}`), `tokens` (dict of 5 labels → JWT string): `m2m_valid`, `m2m_expired`, `m2m_wrong_realm`, `m2m_aud_target`, `authz_valid`. Later tasks read this file.

- [ ] **Step 1: Write `gen_fixtures.py`**

```python
# service/tests/integration/gen_fixtures.py
"""Generate service/tests/integration/fixtures/fixtures.json from the REAL minters.

Standalone (NOT a pytest module). Uses an ephemeral in-memory RSA keypair written to
throwaway temp files, so no private key is ever committed — only the public JWKS +
signed tokens. Re-run via `make realm-fixtures` whenever the token shape changes.
"""

import json
import os
import tempfile
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

_ISSUER = "https://duar.test"

# 1. Ephemeral keypair → temp PEM files (discarded at process exit).
_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_priv_pem = _key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
_pub_pem = (
    _key.public_key()
    .public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    .decode()
)
_tmp = Path(tempfile.mkdtemp())
(_tmp / "priv.pem").write_text(_priv_pem)
(_tmp / "pub.pem").write_text(_pub_pem)

# 2. Point the service key seam at our ephemeral key BEFORE importing src.*.
os.environ["JWT_PRIVATE_KEY_PATH"] = str(_tmp / "priv.pem")
os.environ["JWT_PUBLIC_KEY_PATH"] = str(_tmp / "pub.pem")
os.environ["JWT_PREVIOUS_PUBLIC_KEY_PATHS"] = ""
os.environ["BASE_URL"] = _ISSUER

from src.auth import key_provider  # noqa: E402
from src.auth.jwks import build_jwks  # noqa: E402
from src.auth.jwt import create_authz_token, create_m2m_token  # noqa: E402

key_provider.reset_cache()  # ensure it reads our ephemeral key, not a cached one

_TEN_YEARS = 10 * 365 * 24 * 3600

tokens = {
    "m2m_valid": create_m2m_token(svc="acme-suite", caller="app-a", ttl_s=_TEN_YEARS),
    "m2m_expired": create_m2m_token(svc="acme-suite", caller="app-a", ttl_s=-10),
    "m2m_wrong_realm": create_m2m_token(svc="other-realm", caller="app-a", ttl_s=_TEN_YEARS),
    "m2m_aud_target": create_m2m_token(
        svc="acme-suite", caller="app-a", ttl_s=_TEN_YEARS, aud_target="billing"
    ),
    "authz_valid": create_authz_token(
        user_id=uuid.UUID(int=1),
        idp_sub="google|1",
        workspace_id=uuid.UUID(int=2),
        workspace_slug="acme",
        workspace_role="editor",
        actions=["read"],
        service_name="acme-suite",
        org_id=None,
        org_slug=None,
        org_is_public=False,
    ),
}

fixtures = {
    "issuer": _ISSUER,
    "public_pem": _pub_pem,
    "jwks": build_jwks(),
    "tokens": tokens,
}

out = Path(__file__).parent / "fixtures" / "fixtures.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(fixtures, indent=2) + "\n")
print(f"wrote {out}")
```

- [ ] **Step 2: Add the `realm-fixtures` Makefile target**

In `Makefile`, add to the `.PHONY` list `realm-fixtures`, and add this target (near `docs`):

```makefile
realm-fixtures: ## Regenerate cross-SDK integration test fixtures
	uv run python service/tests/integration/gen_fixtures.py
```

- [ ] **Step 3: Generate the fixtures**

Run: `cd /Users/sidx/workspace/identity-service && uv run python service/tests/integration/gen_fixtures.py`
Expected: prints `wrote …/fixtures/fixtures.json`; the file exists and is valid JSON with `tokens` holding 5 entries.

- [ ] **Step 4: Write the fixtures-correctness test**

```python
# service/tests/integration/test_fixtures.py
"""fixtures.json is internally consistent: tokens decode under its own public key
to the expected claim shapes, and the JWKS carries the signing kid."""

import json
from pathlib import Path

import jwt as pyjwt
import pytest

_FIX = json.loads((Path(__file__).parent / "fixtures" / "fixtures.json").read_text())


def _decode(label: str, aud: str, **opts):
    return pyjwt.decode(
        _FIX["tokens"][label], _FIX["public_pem"], algorithms=["RS256"], audience=aud, **opts
    )


def test_m2m_valid_claim_shape():
    p = _decode("m2m_valid", "sentinel:m2m")
    assert p["type"] == "m2m"
    assert p["svc"] == "acme-suite"
    assert p["caller"] == "app-a"
    assert p["actions"] == ["*"]
    assert p["aud_target"] is None
    assert "sub" not in p  # honest "no human" token


def test_authz_valid_claim_shape():
    p = _decode("authz_valid", "sentinel:authz")
    assert p["type"] == "authz"
    assert p["svc"] == "acme-suite"  # svc is the realm slug, not a bare service name


def test_expired_token_is_actually_expired():
    with pytest.raises(pyjwt.ExpiredSignatureError):
        _decode("m2m_expired", "sentinel:m2m")


def test_wrong_realm_token_has_foreign_svc():
    assert _decode("m2m_wrong_realm", "sentinel:m2m")["svc"] == "other-realm"


def test_aud_target_token_is_targeted():
    assert _decode("m2m_aud_target", "sentinel:m2m")["aud_target"] == "billing"


def test_jwks_contains_the_signing_kid():
    kid = pyjwt.get_unverified_header(_FIX["tokens"]["m2m_valid"])["kid"]
    assert any(k["kid"] == kid for k in _FIX["jwks"]["keys"])
```

- [ ] **Step 5: Run the test**

Run: `cd service && uv run pytest tests/integration/test_fixtures.py -v`
Expected: PASS (6 passed). If `keys/` is needed it is NOT — this test only reads `fixtures.json`.

- [ ] **Step 6: Format + commit**

```bash
cd service && uv run ruff format tests/integration/gen_fixtures.py tests/integration/test_fixtures.py && uv run ruff check --fix tests/integration/gen_fixtures.py tests/integration/test_fixtures.py
cd /Users/sidx/workspace/identity-service
git add service/tests/integration/gen_fixtures.py service/tests/integration/fixtures/fixtures.json service/tests/integration/test_fixtures.py Makefile
git commit -m "test(realm): cross-SDK fixture generator + committed token vectors"
```

---

## Task 2: Python Flow B (m2m) integration — live mint → SDK verify

**Files:**
- Create: `service/tests/integration/test_realm_flow_b.py`

**Interfaces:**
- Consumes: `src.api.realm_routes` (router + `realm_service`), `src.api.dependencies.{ServiceKeyContext, require_service_key}`, `src.database.get_db`, `src.middleware.rate_limit.{limiter, rate_limit_exceeded_handler}`, `src.auth.jwt.{create_m2m_token, create_authz_token}`, `src.auth.key_provider`, `duar_auth.{Duar, SystemAuth}`, `duar_auth.types.DuarError`, and `fixtures.json` from Task 1.
- Produces: nothing for later tasks (terminal test).

- [ ] **Step 1: Write the Flow B test file (valid path first)**

```python
# service/tests/integration/test_realm_flow_b.py
"""Flow B end-to-end: the REAL /realm/m2m-token mint accepted by the REAL SDK verifier.

In-process (TestClient = httpx-over-ASGI, no socket). The realm DB layer + service-key
dependency are faked the same way as tests/test_realm_routes.py; the token itself is
REAL (signed by the ambient key_provider), and the SDK does REAL RS256 verification.
"""

import json
from pathlib import Path

import jwt as pyjwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded

from duar_auth import Duar, SystemAuth
from duar_auth.types import DuarError
from src.api import realm_routes
from src.api.dependencies import ServiceKeyContext, require_service_key
from src.api.realm_routes import router as realm_router
from src.auth import key_provider
from src.auth.jwt import create_authz_token, create_m2m_token
from src.database import get_db
from src.middleware.rate_limit import limiter, rate_limit_exceeded_handler

_FIX = json.loads(
    (Path(__file__).parent / "fixtures" / "fixtures.json").read_text()
)


@pytest.fixture(autouse=True)
def _disable_limiter():
    original = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = original


class _Realm:
    def __init__(self, *, slug="acme-suite", name="Acme Suite", m2m_ttl_s=300, is_active=True):
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
        service_name="app-a", realm_slug=realm_slug
    )

    async def _db():
        yield None

    app.dependency_overrides[get_db] = _db
    return app


def _pubpem_for(token: str) -> str:
    """The verifying public PEM for a real token, by its kid (ambient signing key)."""
    kid = pyjwt.get_unverified_header(token)["kid"]
    return key_provider.verification_keys()[kid]


def _sdk(*, effective_scope: str, public_pem: str, service_name: str = "app-b") -> Duar:
    s = Duar(
        base_url="https://duar.test",
        service_name=service_name,
        service_key="svc-key",
        idp_public_key="-----BEGIN PUBLIC KEY-----\nx\n-----END PUBLIC KEY-----",
        idp_audience="client-id",
    )
    s._duar_public_key = public_pem
    s._effective_scope = effective_scope
    return s


def test_flow_b_real_mint_accepted_by_sdk(monkeypatch):
    async def _get(_db, slug):
        return _Realm(slug=slug, m2m_ttl_s=300, is_active=True)

    monkeypatch.setattr(realm_routes.realm_service, "get_realm_by_slug", _get)
    monkeypatch.setattr(realm_routes, "log_security", lambda *a, **k: None)

    resp = TestClient(_build_app(realm_slug="acme-suite")).post("/realm/m2m-token", json={})
    assert resp.status_code == 200
    token = resp.json()["token"]

    sdk = _sdk(effective_scope="acme-suite", public_pem=_pubpem_for(token))
    sys_auth = sdk.verify_m2m_token(token)
    assert isinstance(sys_auth, SystemAuth)
    assert sys_auth.caller == "app-a"  # server-stamped from the authenticated key
    assert sys_auth.svc == "acme-suite"
    assert sys_auth.can("anything") is True
```

- [ ] **Step 2: Run it (valid path)**

Run: `cd service && uv run pytest tests/integration/test_realm_flow_b.py -v`
Expected: PASS (1 passed). (Requires dev `keys/` present — the real mint signs via `key_provider`; this matches the existing `tests/test_realm_routes.py` assumption.)

- [ ] **Step 3: Append the negative cases**

```python
def test_flow_b_wrong_realm_rejected(monkeypatch):
    async def _get(_db, slug):
        return _Realm(slug=slug)

    monkeypatch.setattr(realm_routes.realm_service, "get_realm_by_slug", _get)
    monkeypatch.setattr(realm_routes, "log_security", lambda *a, **k: None)
    resp = TestClient(_build_app(realm_slug="acme-suite")).post("/realm/m2m-token", json={})
    token = resp.json()["token"]
    # Receiver's effective_scope is a DIFFERENT realm → cross-realm replay must fail.
    sdk = _sdk(effective_scope="other-realm", public_pem=_pubpem_for(token))
    with pytest.raises(DuarError) as exc:
        sdk.verify_m2m_token(token)
    assert exc.value.status_code == 403


def test_flow_b_non_member_cannot_mint(monkeypatch):
    monkeypatch.setattr(realm_routes, "log_security", lambda *a, **k: None)
    resp = TestClient(_build_app(realm_slug=None)).post("/realm/m2m-token", json={})
    assert resp.status_code == 403  # standalone service has no realm to mint under


def test_flow_b_expired_rejected():
    token = create_m2m_token(svc="acme-suite", caller="app-a", ttl_s=-10)
    sdk = _sdk(effective_scope="acme-suite", public_pem=_pubpem_for(token))
    with pytest.raises(DuarError) as exc:
        sdk.verify_m2m_token(token)
    assert exc.value.status_code == 401


def test_flow_b_authz_token_rejected_as_m2m():
    # Token-type-confusion defense: a real authz token (aud=sentinel:authz) must never
    # validate through the m2m verifier (aud=sentinel:m2m).
    import uuid

    token = create_authz_token(
        user_id=uuid.UUID(int=1),
        idp_sub="google|1",
        workspace_id=uuid.UUID(int=2),
        workspace_slug="acme",
        workspace_role="editor",
        actions=["read"],
        service_name="acme-suite",
        org_id=None,
        org_slug=None,
        org_is_public=False,
    )
    sdk = _sdk(effective_scope="acme-suite", public_pem=_pubpem_for(token))
    with pytest.raises(DuarError):
        sdk.verify_m2m_token(token)


def test_flow_b_aud_target_mismatch_rejected():
    token = create_m2m_token(svc="acme-suite", caller="app-a", ttl_s=300, aud_target="billing")
    # Receiver is "reports", token targets "billing" → reject.
    sdk = _sdk(effective_scope="acme-suite", public_pem=_pubpem_for(token), service_name="reports")
    with pytest.raises(DuarError) as exc:
        sdk.verify_m2m_token(token)
    assert exc.value.status_code == 403
```

- [ ] **Step 4: Append the committed-fixture guard (protects the JS side)**

```python
def test_committed_fixtures_accepted_and_negatives_rejected():
    """The committed fixtures.json the JS test consumes still verifies under the SDK."""
    sdk = _sdk(
        effective_scope="acme-suite", public_pem=_FIX["public_pem"], service_name="reports"
    )
    ok = sdk.verify_m2m_token(_FIX["tokens"]["m2m_valid"])
    assert ok.svc == "acme-suite"
    assert ok.caller == "app-a"
    for label in ("m2m_expired", "m2m_wrong_realm", "authz_valid", "m2m_aud_target"):
        with pytest.raises(DuarError):
            sdk.verify_m2m_token(_FIX["tokens"][label])


def test_committed_m2m_claims_match_current_minter():
    """Freshness guard: if the service minter's claim set drifts and fixtures.json is
    not regenerated, this fails — keeping the JS vector honest. Compares claim KEYS
    (and aud/type), not volatile values (iat/exp/jti)."""
    committed = pyjwt.decode(
        _FIX["tokens"]["m2m_valid"], _FIX["public_pem"], algorithms=["RS256"], audience="sentinel:m2m"
    )
    fresh_token = create_m2m_token(svc="acme-suite", caller="app-a", ttl_s=300)
    fresh = pyjwt.decode(
        fresh_token, _pubpem_for(fresh_token), algorithms=["RS256"], audience="sentinel:m2m"
    )
    assert sorted(committed.keys()) == sorted(fresh.keys())
    assert committed["aud"] == fresh["aud"] == "sentinel:m2m"
    assert committed["type"] == fresh["type"] == "m2m"
```

- [ ] **Step 5: Run the full Flow B file**

Run: `cd service && uv run pytest tests/integration/test_realm_flow_b.py -v`
Expected: PASS (8 passed).

- [ ] **Step 6: Format + commit**

```bash
cd service && uv run ruff format tests/integration/test_realm_flow_b.py && uv run ruff check --fix tests/integration/test_realm_flow_b.py
cd /Users/sidx/workspace/identity-service
git add service/tests/integration/test_realm_flow_b.py
git commit -m "test(realm): Flow B integration — real m2m mint accepted by real SDK verify"
```

---

## Task 3: Python Flow A (authz) integration — real mint → AuthzMiddleware

**Files:**
- Create: `service/tests/integration/test_realm_flow_a.py`

**Interfaces:**
- Consumes: `src.auth.jwt.create_authz_token`, `src.auth.key_provider`, `duar_auth.authz_middleware.AuthzMiddleware`, plus `cryptography` + `jwt` for an in-test IdP keypair. Mirrors the two-keypair `TestClient` pattern of `sdk/tests/test_authz_effective_scope.py` but feeds a REAL service-minted authz token.
- Produces: nothing for later tasks (terminal test).

- [ ] **Step 1: Write the Flow A test**

```python
# service/tests/integration/test_realm_flow_a.py
"""Flow A end-to-end: a REAL service-minted authz token (svc = realm slug) accepted by
the REAL Python AuthzMiddleware when the receiver's effective_scope is that slug, and
rejected when the token was minted for a different scope.

The authz token comes from the real create_authz_token minter (signed by the ambient
key_provider). The IdP token is minted with an in-test keypair — AuthzMiddleware is a
dual-token gate (IdP identity + Duar authz), so both are required.
"""

import datetime
import uuid

import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from duar_auth.authz_middleware import AuthzMiddleware
from src.auth import key_provider
from src.auth.jwt import create_authz_token

_IDP_SUB = "google|1"
_IDP_AUD = "client-id"


def _idp_keypair():
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = (
        k.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    priv = k.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return priv, pub


def _idp_token(idp_priv: str) -> str:
    now = datetime.datetime.now(datetime.UTC)
    return pyjwt.encode(
        {
            "sub": _IDP_SUB,
            "aud": _IDP_AUD,
            "email": "a@b.com",
            "name": "A",
            "iat": now,
            "exp": now + datetime.timedelta(hours=1),
        },
        idp_priv,
        algorithm="RS256",
    )


def _real_authz_token(*, service_name: str) -> str:
    """A REAL Duar authz token, svc=service_name, bound to the IdP identity."""
    return create_authz_token(
        user_id=uuid.UUID(int=1),
        idp_sub=_IDP_SUB,
        workspace_id=uuid.UUID(int=2),
        workspace_slug="acme",
        workspace_role="editor",
        actions=["read"],
        service_name=service_name,
        org_id=None,
        org_slug=None,
        org_is_public=False,
    )


def _duar_pubpem(token: str) -> str:
    kid = pyjwt.get_unverified_header(token)["kid"]
    return key_provider.verification_keys()[kid]


class _FakeInstance:
    """Minimal Duar stand-in: supplies effective_scope; no JWKS (static-key mode)."""

    idp_jwks_url = None

    def __init__(self, effective_scope: str):
        self.effective_scope = effective_scope


def _app(*, effective_scope: str, idp_pub: str, duar_pub: str) -> Starlette:
    async def protected(request: Request) -> JSONResponse:
        return JSONResponse({"role": request.state.user.workspace_role})

    app = Starlette(routes=[Route("/protected", protected)])
    app.add_middleware(
        AuthzMiddleware,
        service_name="app-b",  # the member's own name — NOT the realm slug
        idp_audience=_IDP_AUD,
        idp_public_key=idp_pub,
        duar_public_key=duar_pub,
        duar_instance=_FakeInstance(effective_scope),
    )
    return app


def test_flow_a_realm_member_accepts_realm_scoped_token():
    idp_priv, idp_pub = _idp_keypair()
    authz = _real_authz_token(service_name="acme-suite")  # minted for the realm slug
    client = TestClient(
        _app(effective_scope="acme-suite", idp_pub=idp_pub, duar_pub=_duar_pubpem(authz))
    )
    resp = client.get(
        "/protected",
        headers={"Authorization": f"Bearer {_idp_token(idp_priv)}", "X-Authz-Token": authz},
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "editor"


def test_flow_a_member_rejects_token_minted_for_other_scope():
    idp_priv, idp_pub = _idp_keypair()
    authz = _real_authz_token(service_name="other-realm")  # wrong scope
    client = TestClient(
        _app(effective_scope="acme-suite", idp_pub=idp_pub, duar_pub=_duar_pubpem(authz))
    )
    resp = client.get(
        "/protected",
        headers={"Authorization": f"Bearer {_idp_token(idp_priv)}", "X-Authz-Token": authz},
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: Run it**

Run: `cd service && uv run pytest tests/integration/test_realm_flow_a.py -v`
Expected: PASS (2 passed). (Requires dev `keys/` — the authz token is signed by `key_provider`.)

- [ ] **Step 3: Format + commit**

```bash
cd service && uv run ruff format tests/integration/test_realm_flow_a.py && uv run ruff check --fix tests/integration/test_realm_flow_a.py
cd /Users/sidx/workspace/identity-service
git add service/tests/integration/test_realm_flow_a.py
git commit -m "test(realm): Flow A integration — real authz token accepted by real AuthzMiddleware"
```

---

## Task 4: JS integration — real `jose` verifies the shared bytes

**Files:**
- Create: `sdks/js/src/__tests__/realm-integration.test.ts`

**Interfaces:**
- Consumes: `../m2m` (`verifyM2mToken`), the committed `fixtures.json` (Task 1), real `jose`.
- Produces: nothing (terminal test). **Must NOT** `vi.mock('jose')`.

- [ ] **Step 1: Write the JS integration test**

```typescript
// sdks/js/src/__tests__/realm-integration.test.ts
// Cross-SDK: the SAME bytes a Python Duar minted, verified by REAL jose (no mock).
// jose's createRemoteJWKSet fetches the JWKS URL — we stub global.fetch to serve the
// committed fixture JWKS, so the crypto path (RS256 + kid resolution) is fully real;
// only the key *transport* is stubbed.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { verifyM2mToken } from '../m2m'
import fixtures from '../../../../service/tests/integration/fixtures/fixtures.json'

const JWKS_URL = 'http://duar-internal:9010/.well-known/jwks.json'

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(JSON.stringify(fixtures.jwks), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    ),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('cross-SDK m2m (real jose)', () => {
  it('accepts a real Duar-minted m2m token', async () => {
    const sys = await verifyM2mToken(fixtures.tokens.m2m_valid, {
      jwksUrl: JWKS_URL,
      effectiveScope: 'acme-suite',
      serviceName: 'reports',
    })
    expect(sys.caller).toBe('app-a')
    expect(sys.svc).toBe('acme-suite')
    expect(sys.can('anything')).toBe(true)
  })

  it('rejects a token minted for another realm', async () => {
    await expect(
      verifyM2mToken(fixtures.tokens.m2m_wrong_realm, {
        jwksUrl: JWKS_URL,
        effectiveScope: 'acme-suite',
      }),
    ).rejects.toThrow(/realm/i)
  })

  it('rejects an expired token', async () => {
    await expect(
      verifyM2mToken(fixtures.tokens.m2m_expired, {
        jwksUrl: JWKS_URL,
        effectiveScope: 'acme-suite',
      }),
    ).rejects.toThrow()
  })

  it('rejects a real authz token (wrong audience) through the m2m verifier', async () => {
    await expect(
      verifyM2mToken(fixtures.tokens.authz_valid, {
        jwksUrl: JWKS_URL,
        effectiveScope: 'acme-suite',
      }),
    ).rejects.toThrow()
  })

  it('rejects a token targeted at a different service', async () => {
    await expect(
      verifyM2mToken(fixtures.tokens.m2m_aud_target, {
        jwksUrl: JWKS_URL,
        effectiveScope: 'acme-suite',
        serviceName: 'reports',
      }),
    ).rejects.toThrow(/target/i)
  })
})
```

- [ ] **Step 2: Run the JS suite**

Run: `cd sdks/js && npm test`
Expected: all PASS — the new `realm-integration.test.ts` (5 tests, real jose) plus the existing mocked `m2m.test.ts` (per-file `vi.mock` keeps them isolated). If `node_modules` is missing, `npm install` first.

- [ ] **Step 3: Build (typecheck) to confirm the JSON import resolves**

Run: `cd sdks/js && npm run build`
Expected: build succeeds. If the JSON import errors on `resolveJsonModule`, confirm `tsconfig` has it (vite/vitest enable JSON imports by default); do NOT add new deps — if needed, read the JSON via `fs` in the test instead (it runs in Node).

- [ ] **Step 4: Commit**

```bash
cd /Users/sidx/workspace/identity-service
git add sdks/js/src/__tests__/realm-integration.test.ts
git commit -m "test(js-sdk): cross-SDK realm integration — real jose verifies Python-minted tokens"
```

---

## Self-review (done by plan author)

**Spec coverage:**
- Flow A contract (real authz mint → AuthzMiddleware accept/reject) → Task 3.
- Flow B contract (real `/realm/m2m-token` → `verify_m2m_token` → SystemAuth) + negatives (wrong-realm, expired, non-member, wrong-aud, aud_target) → Task 2.
- Same bytes accepted by real (un-mocked) JS `verifyM2mToken` + negatives → Task 4.
- Claim-drift catch: Task 2 mints live via the real minter (`test_flow_b_real_mint_accepted_by_sdk`) and the freshness guard (`test_committed_m2m_claims_match_current_minter`) compares the committed vector's claim set against a freshly-minted one.
- JS real-crypto gap: Task 4 un-mocks `jose`.
- `gen_fixtures.py` + committed `fixtures.json` (5 labelled tokens, JWKS, public PEM) → Task 1.
- No committed private key (ephemeral keypair) → Task 1 Step 1. No dep change → Global Constraints. `nextjs` excluded (re-exports `@duar-auth/js`, covered) → matches spec Non-Goals.

**Placeholder scan:** none — every code/command step carries full content. The one conditional (Task 4 Step 3 JSON-import fallback) names the concrete alternative (`fs` read in Node) rather than deferring.

**Type/name consistency:** `create_m2m_token(svc, caller, ttl_s, …, aud_target=)` and `create_authz_token(…, service_name, …)` match `jwt.py`; `ServiceKeyContext(service_name, realm_slug)` and the override + `_Realm` + `realm_service.get_realm_by_slug` monkeypatch match `tests/test_realm_routes.py`; `Duar(base_url, service_name, service_key, idp_public_key, idp_audience)` + `_duar_public_key`/`_effective_scope` + `verify_m2m_token`→`DuarError(.status_code)` match `duar.py`; `AuthzMiddleware(service_name, idp_audience, idp_public_key, duar_public_key, duar_instance)` + `_FakeInstance(effective_scope)` + headers `Authorization`/`X-Authz-Token` + `request.state.user.workspace_role` match `authz_middleware.py` and `test_authz_effective_scope.py`; JS `verifyM2mToken(token, {jwksUrl, effectiveScope, serviceName})` + `{caller, svc, can}` match `m2m.ts`. The `kid → key_provider.verification_keys()[kid]` lookup matches `key_provider.py`; `build_jwks()` shape `{"keys":[…]}` matches `jwks.py`.

**Reused-not-added:** reuses the `tests/test_realm_routes.py` override/`_Realm` pattern, the `test_authz_effective_scope.py` two-keypair `_FakeInstance` pattern, the existing `vi.stubGlobal('fetch', …)` style from `m2m.test.ts`, and the shared workspace venv. No new dependencies, no `pyproject` edits, no committed secrets.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-26-realm-cross-sdk-integration-tests.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task (1→4), two-stage review between tasks.
2. **Inline Execution** — execute in this session with checkpoints.
