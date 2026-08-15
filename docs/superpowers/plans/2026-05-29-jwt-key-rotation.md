# JWT Signing-Key Rotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Duar graceful RS256 signing-key rotation — sign with a current key (stamped with a `kid`), verify against current + retired keys by `kid`, publish all keys in JWKS, and make the SDK pick up new keys without a restart.

**Architecture:** A thin `key_provider` seam returns the current signing key and a `{kid: public_pem}` verification set (KMS can replace it later). `jwt.py` stamps a `kid` header and verifies strictly by `kid`. `jwks.py` publishes every verification key. Both SDK middlewares resolve the verifying key by `kid` from a cached keyset, refetching JWKS on an unknown `kid`.

**Tech Stack:** Python 3.12, PyJWT, cryptography, FastAPI/Starlette, pytest (+ respx in the SDK), uv.

**Decisions (from spec):** PEM files behind a provider seam; manual rotation + runbook; RFC-7638 thumbprint `kid`; **strict `kid`** (no legacy fallback — all clients are in dev and re-login once).

---

### Task 1: Config — retired-key paths

**Files:**
- Modify: `service/src/config.py:22-29` (JWT block) and the properties section
- Test: `service/tests/test_key_rotation.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# service/tests/test_key_rotation.py
from src.config import Settings


def test_previous_public_key_paths_parses_csv():
    s = Settings(jwt_previous_public_key_paths="keys/old1.pem, keys/old2.pem")
    assert s.jwt_previous_public_key_paths_list == ["keys/old1.pem", "keys/old2.pem"]


def test_previous_public_key_paths_empty_default():
    s = Settings()
    assert s.jwt_previous_public_key_paths_list == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_key_rotation.py -v`
Expected: FAIL (`AttributeError: 'Settings' object has no attribute 'jwt_previous_public_key_paths_list'`)

- [ ] **Step 3: Add the field and property**

In `service/src/config.py`, after line 29 (`authz_token_expire_minutes: int = 5`) add:

```python
    jwt_previous_public_key_paths: str = ""  # comma-separated retired public key paths (verify-only)
```

In the properties section (after `cors_origin_list`, around line 84) add:

```python
    @property
    def jwt_previous_public_key_paths_list(self) -> list[str]:
        if not self.jwt_previous_public_key_paths:
            return []
        return [p.strip() for p in self.jwt_previous_public_key_paths.split(",") if p.strip()]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd service && uv run pytest tests/test_key_rotation.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add service/src/config.py service/tests/test_key_rotation.py
git commit -m "feat(config): add JWT_PREVIOUS_PUBLIC_KEY_PATHS for key rotation

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Key provider seam

**Files:**
- Create: `service/src/auth/key_provider.py`
- Test: `service/tests/test_key_rotation.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `service/tests/test_key_rotation.py`:

```python
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def _write_keypair(tmp_path, name):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    priv_path = tmp_path / f"{name}_priv.pem"
    pub_path = tmp_path / f"{name}_pub.pem"
    priv_path.write_bytes(priv)
    pub_path.write_bytes(pub)
    return priv_path, pub_path


@pytest.fixture
def two_keys(tmp_path, monkeypatch):
    from src.auth import key_provider
    from src.config import settings

    cur_priv, cur_pub = _write_keypair(tmp_path, "cur")
    old_priv, old_pub = _write_keypair(tmp_path, "old")
    monkeypatch.setattr(settings, "jwt_private_key_path", cur_priv)
    monkeypatch.setattr(settings, "jwt_public_key_path", cur_pub)
    monkeypatch.setattr(settings, "jwt_previous_public_key_paths", str(old_pub))
    key_provider.reset_cache()
    yield {"cur_pub": cur_pub.read_text(), "old_pub": old_pub.read_text()}
    key_provider.reset_cache()


def test_signing_key_returns_private_and_kid(two_keys):
    from src.auth import key_provider

    private_pem, kid = key_provider.signing_key()
    assert "PRIVATE KEY" in private_pem
    assert kid == key_provider.thumbprint_kid(two_keys["cur_pub"])


def test_verification_keys_include_current_and_previous(two_keys):
    from src.auth import key_provider

    keys = key_provider.verification_keys()
    cur_kid = key_provider.thumbprint_kid(two_keys["cur_pub"])
    old_kid = key_provider.thumbprint_kid(two_keys["old_pub"])
    assert set(keys) == {cur_kid, old_kid}
    assert keys[cur_kid] == two_keys["cur_pub"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_key_rotation.py -k provider_or_keys -v`
(Or run the whole file.) Expected: FAIL (`ModuleNotFoundError: src.auth.key_provider`)

- [ ] **Step 3: Create the provider**

```python
# service/src/auth/key_provider.py
"""Signing/verification key provider.

The single seam between key *material* and the rest of the auth layer. Today it
reads PEM files from disk; a future KMS/HSM implementation can replace these
functions without touching token, JWKS, or verify logic.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives.serialization import load_pem_public_key
from jwt.algorithms import RSAAlgorithm

from src.config import settings

_signing_cache: tuple[str, str] | None = None
_verification_cache: dict[str, str] | None = None


def thumbprint_kid(public_pem: str) -> str:
    """RFC 7638 JWK thumbprint of an RSA public key, used as its kid."""
    pub = load_pem_public_key(public_pem.encode())
    jwk = json.loads(RSAAlgorithm.to_jwk(pub))
    thumbprint_input = json.dumps(
        {"e": jwk["e"], "kty": "RSA", "n": jwk["n"]},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return (
        base64.urlsafe_b64encode(hashlib.sha256(thumbprint_input).digest())
        .rstrip(b"=")
        .decode("ascii")
    )


def signing_key() -> tuple[str, str]:
    """Return (private_pem, kid) for the current signing key."""
    global _signing_cache
    if _signing_cache is None:
        private_pem = settings.jwt_private_key_path.read_text()
        public_pem = settings.jwt_public_key_path.read_text()
        _signing_cache = (private_pem, thumbprint_kid(public_pem))
    return _signing_cache


def verification_keys() -> dict[str, str]:
    """Return {kid: public_pem} for the current key plus any retired keys."""
    global _verification_cache
    if _verification_cache is None:
        keys: dict[str, str] = {}
        current_pub = settings.jwt_public_key_path.read_text()
        keys[thumbprint_kid(current_pub)] = current_pub
        for path in settings.jwt_previous_public_key_paths_list:
            pem = Path(path).read_text()
            keys[thumbprint_kid(pem)] = pem
        _verification_cache = keys
    return _verification_cache


def reset_cache() -> None:
    """Clear cached key material (after rotation/reload and in tests)."""
    global _signing_cache, _verification_cache
    _signing_cache = None
    _verification_cache = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd service && uv run pytest tests/test_key_rotation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add service/src/auth/key_provider.py service/tests/test_key_rotation.py
git commit -m "feat(auth): add key_provider seam with thumbprint kid + verify set

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Sign with `kid`, verify strictly by `kid`

**Files:**
- Modify: `service/src/auth/jwt.py` (sign helper + all `create_*` + `decode_token`, remove file-reading globals)
- Test: `service/tests/test_key_rotation.py` (append)

- [ ] **Step 1: Write the failing test**

Append:

```python
import jwt as pyjwt
import uuid


def test_issued_token_has_kid(two_keys):
    from src.auth import key_provider
    from src.auth.jwt import create_authz_token

    token = create_authz_token(
        user_id=uuid.uuid4(), idp_sub="google|1", workspace_id=uuid.uuid4(),
        workspace_slug="w", workspace_role="viewer", actions=[], service_name="svc",
    )
    header = pyjwt.get_unverified_header(token)
    _, expected_kid = key_provider.signing_key()
    assert header["kid"] == expected_kid


def test_decode_rejects_token_without_kid(two_keys):
    from src.auth import key_provider
    from src.auth.jwt import _AUD_AUTHZ, decode_token
    from src.config import settings

    private_pem, _ = key_provider.signing_key()
    payload = {
        "iss": settings.base_url, "sub": str(uuid.uuid4()), "aud": _AUD_AUTHZ,
        "type": "authz", "exp": pyjwt.api_jwt.datetime.datetime.now(pyjwt.api_jwt.datetime.UTC)
        + pyjwt.api_jwt.datetime.timedelta(minutes=5),
    }
    no_kid = pyjwt.encode(payload, private_pem, algorithm="RS256")  # no headers={"kid":...}
    with pytest.raises(pyjwt.InvalidTokenError):
        decode_token(no_kid, audience=_AUD_AUTHZ)


def test_decode_rejects_unknown_kid(two_keys):
    from src.auth.jwt import _AUD_AUTHZ, create_authz_token, decode_token
    from src.auth import key_provider
    from src.config import settings

    token = create_authz_token(
        user_id=uuid.uuid4(), idp_sub="google|1", workspace_id=uuid.uuid4(),
        workspace_slug="w", workspace_role="viewer", actions=[], service_name="svc",
    )
    # Drop all keys from the verify set so the token's kid is unknown.
    monkey = key_provider._verification_cache
    key_provider._verification_cache = {}
    try:
        with pytest.raises(pyjwt.InvalidTokenError):
            decode_token(token, audience=_AUD_AUTHZ)
    finally:
        key_provider._verification_cache = monkey
```

> Note: the existing `tests/test_authz_jwt.py` round-trip tests must still pass — they use the real `keys/` and now exercise the kid path.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_key_rotation.py -v`
Expected: FAIL (tokens have no `kid`; decode still uses single key)

- [ ] **Step 3: Rewrite `jwt.py` signing + decode**

Replace lines 8-27 (the `_private_key`/`_public_key` globals and `_get_private_key`/`_get_public_key`/`get_public_key`) with:

```python
from src.auth import key_provider


def _sign(payload: dict) -> str:
    private_pem, kid = key_provider.signing_key()
    return jwt.encode(
        payload, private_pem, algorithm=settings.jwt_algorithm, headers={"kid": kid}
    )


def get_public_key() -> str:
    """Current signing key's public PEM (kept for back-compat)."""
    _, kid = key_provider.signing_key()
    return key_provider.verification_keys()[kid]
```

In each of `create_access_token`, `create_admin_token`, `create_refresh_token`, `create_authz_token`, replace the final
`return jwt.encode(payload, _get_private_key(), algorithm=settings.jwt_algorithm)`
with:
`return _sign(payload)`

Replace `decode_token` (lines 132-144) with:

```python
def decode_token(token: str, audience: str | list[str]) -> dict:
    """Decode and validate a JWT, selecting the verifying key by its kid.

    Audience is required. Algorithm is hardcoded to RS256. A token whose kid is
    missing or not in the current verification set is rejected (strict).
    """
    kid = jwt.get_unverified_header(token).get("kid")
    keys = key_provider.verification_keys()
    if not kid or kid not in keys:
        raise jwt.InvalidTokenError("Unknown or missing key id")
    return jwt.decode(
        token,
        keys[kid],
        algorithms=["RS256"],  # Security: hardcode to prevent algorithm substitution
        audience=audience,
        issuer=_ISSUER,
    )
```

- [ ] **Step 4: Verify no other module imports the removed helpers**

Run: `cd service && grep -rn "_get_private_key\|_get_public_key" src/ tests/`
Expected: no matches outside `jwt.py` history. If any appear, repoint them to `key_provider.signing_key()` / `get_public_key()`.

- [ ] **Step 5: Run tests**

Run: `cd service && uv run pytest tests/test_key_rotation.py tests/test_authz_jwt.py -v`
Expected: PASS (new kid tests pass; existing round-trip tests still pass)

- [ ] **Step 6: Commit**

```bash
git add service/src/auth/jwt.py service/tests/test_key_rotation.py
git commit -m "feat(auth): stamp kid on tokens and verify strictly by kid

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Multi-key JWKS

**Files:**
- Modify: `service/src/auth/jwks.py` (build from provider, drop local thumbprint + get_public_key import)
- Test: `service/tests/test_key_rotation.py` (append)

- [ ] **Step 1: Write the failing test**

```python
def test_jwks_publishes_all_verification_keys(two_keys):
    from src.auth import jwks, key_provider

    jwks._jwks_cache = None  # bypass TTL cache
    out = jwks.build_jwks()
    published = {k["kid"] for k in out["keys"]}
    assert published == set(key_provider.verification_keys())
    for k in out["keys"]:
        assert k["use"] == "sig" and k["alg"] == "RS256" and k["kty"] == "RSA"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_key_rotation.py::test_jwks_publishes_all_verification_keys -v`
Expected: FAIL (only one key published)

- [ ] **Step 3: Rewrite `build_jwks`**

Replace the body of `build_jwks()` (and drop `from src.auth.jwt import get_public_key`, add `from src.auth import key_provider`):

```python
def build_jwks() -> dict:
    """Build a JWKS response from all current + retired verification keys."""
    global _jwks_cache, _jwks_cache_time
    if (
        _jwks_cache is not None
        and (time.monotonic() - _jwks_cache_time) < _JWKS_CACHE_TTL
    ):
        return _jwks_cache

    keys = []
    for kid, public_pem in key_provider.verification_keys().items():
        pub_key = load_pem_public_key(public_pem.encode())
        jwk = json.loads(RSAAlgorithm.to_jwk(pub_key))
        jwk["use"] = "sig"
        jwk["alg"] = "RS256"
        jwk["kid"] = kid
        keys.append(jwk)

    _jwks_cache = {"keys": keys}
    _jwks_cache_time = time.monotonic()
    return _jwks_cache
```

Remove the now-unused `base64` / `hashlib` imports only if nothing else uses them in the file (the thumbprint moved to `key_provider`).

- [ ] **Step 4: Run tests**

Run: `cd service && uv run pytest tests/test_key_rotation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add service/src/auth/jwks.py service/tests/test_key_rotation.py
git commit -m "feat(auth): publish all verification keys in JWKS with kids

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Server rotation-continuity test

**Files:**
- Test: `service/tests/test_key_rotation.py` (append) — no production code change

- [ ] **Step 1: Write the test**

```python
def test_rotation_continuity(two_keys, tmp_path, monkeypatch):
    """Token signed by the OLD key still verifies while it is in the verify set,
    then fails once it is dropped."""
    from src.auth import key_provider
    from src.auth.jwt import _AUD_AUTHZ, create_authz_token, decode_token
    from src.config import settings

    # 1. Sign a token under the current key, then "rotate": make a brand-new
    #    current key while the just-used key becomes a previous (verify-only) key.
    old_pub_pem = two_keys["cur_pub"]
    token_from_old = create_authz_token(
        user_id=uuid.uuid4(), idp_sub="google|1", workspace_id=uuid.uuid4(),
        workspace_slug="w", workspace_role="viewer", actions=[], service_name="svc",
    )

    new_priv, new_pub = _write_keypair(tmp_path, "new")
    old_pub_path = tmp_path / "rotated_old_pub.pem"
    old_pub_path.write_text(old_pub_pem)
    monkeypatch.setattr(settings, "jwt_private_key_path", new_priv)
    monkeypatch.setattr(settings, "jwt_public_key_path", new_pub)
    monkeypatch.setattr(settings, "jwt_previous_public_key_paths", str(old_pub_path))
    key_provider.reset_cache()

    # New tokens use the new key; the old token still verifies (old key retained).
    assert decode_token(token_from_old, audience=_AUD_AUTHZ)["svc"] == "svc"
    new_token = create_authz_token(
        user_id=uuid.uuid4(), idp_sub="google|1", workspace_id=uuid.uuid4(),
        workspace_slug="w", workspace_role="viewer", actions=[], service_name="svc",
    )
    assert decode_token(new_token, audience=_AUD_AUTHZ)["svc"] == "svc"

    # 2. Drop the old key from the verify set → old token now rejected.
    monkeypatch.setattr(settings, "jwt_previous_public_key_paths", "")
    key_provider.reset_cache()
    with pytest.raises(pyjwt.InvalidTokenError):
        decode_token(token_from_old, audience=_AUD_AUTHZ)
    assert decode_token(new_token, audience=_AUD_AUTHZ)["svc"] == "svc"
```

- [ ] **Step 2: Run test**

Run: `cd service && uv run pytest tests/test_key_rotation.py::test_rotation_continuity -v`
Expected: PASS

- [ ] **Step 3: Run the full server suite (regression)**

Run: `cd service && uv run pytest -q`
Expected: PASS. If any test hand-signs a Duar token without a `kid` and now fails, update it to use the `create_*` helpers.

- [ ] **Step 4: Commit**

```bash
git add service/tests/test_key_rotation.py
git commit -m "test(auth): verify graceful key-rotation continuity

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: SDK `JWTAuthMiddleware` — keyset by `kid` + refetch on miss

**Files:**
- Modify: `sdk/src/duar_auth/middleware.py` (`__init__`, replace `_get_public_key`, `dispatch`)
- Test: `sdk/tests/test_middleware.py` (append a JWKS-rotation class)

- [ ] **Step 1: Write the failing test**

Append to `sdk/tests/test_middleware.py`:

```python
import json

import respx
from httpx import Response
from jwt.algorithms import RSAAlgorithm


def _jwks_for(public_pem: str, kid: str) -> dict:
    jwk = json.loads(RSAAlgorithm.to_jwk(__import__("cryptography.hazmat.primitives.serialization", fromlist=["load_pem_public_key"]).load_pem_public_key(public_pem.encode())))
    jwk.update({"use": "sig", "alg": "RS256", "kid": kid})
    return {"keys": [jwk]}


class TestJWKSRotation:
    @respx.mock
    def test_selects_key_by_kid_from_jwks(self, rsa_keypair, jwt_payload):
        import jwt as pyjwt

        priv, pub = rsa_keypair
        kid = "key-1"
        token = pyjwt.encode(jwt_payload, priv, algorithm="RS256", headers={"kid": kid})
        respx.get("http://duar/.well-known/jwks.json").mock(
            return_value=Response(200, json=_jwks_for(pub, kid))
        )

        app = _make_jwks_app("http://duar")
        client = TestClient(app)
        resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    @respx.mock
    def test_refetches_jwks_on_unknown_kid(self, rsa_keypair, jwt_payload):
        import jwt as pyjwt
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa as rsa_mod

        # Old key is what JWKS serves first; new key arrives after "rotation".
        old_priv, old_pub = rsa_keypair
        new = rsa_mod.generate_private_key(public_exponent=65537, key_size=2048)
        new_pub = new.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()
        new_priv = new.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode()

        route = respx.get("http://duar/.well-known/jwks.json")
        route.side_effect = [
            Response(200, json=_jwks_for(old_pub, "old")),   # first fetch (caches "old")
            Response(200, json=_jwks_for(new_pub, "new")),   # refetch after unknown kid
        ]

        token_new = pyjwt.encode(jwt_payload, new_priv, algorithm="RS256", headers={"kid": "new"})
        app = _make_jwks_app("http://duar")
        client = TestClient(app)
        # First request carries an unknown kid → middleware must refetch and succeed.
        resp = client.get("/protected", headers={"Authorization": f"Bearer {token_new}"})
        assert resp.status_code == 200
```

Add this helper near `_make_app` in the same file:

```python
def _make_jwks_app(base_url: str) -> Starlette:
    async def protected(request: Request) -> JSONResponse:
        return JSONResponse({"email": request.state.user.email})

    app = Starlette(routes=[Route("/protected", protected)])
    app.add_middleware(JWTAuthMiddleware, base_url=base_url)
    return app
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sdk && uv run pytest tests/test_middleware.py::TestJWKSRotation -v`
Expected: FAIL (current code caches `keys[0]`, never refetches, ignores `kid`)

- [ ] **Step 3: Rewrite the middleware key handling**

In `sdk/src/duar_auth/middleware.py`, replace `self.public_key = public_key` line and the `_get_public_key` method with a keyset. In `__init__` (after `self.jwks_url = jwks_url`) keep `self._jwks_lock = asyncio.Lock()` and add `self._keyset: dict | None = None`. Replace `_get_public_key` with:

```python
    async def _key_for_token(self, token: str):
        """Resolve the verifying key. Static public_key mode ignores kid;
        JWKS mode selects by kid and refetches once on an unknown kid."""
        if self.public_key:
            return self.public_key
        kid = jwt.get_unverified_header(token).get("kid")
        if not kid:
            raise jwt.InvalidTokenError("Token missing kid")
        if self._keyset is None or kid not in self._keyset:
            async with self._jwks_lock:
                if self._keyset is None or kid not in self._keyset:
                    await self._refresh_keyset()
        key = (self._keyset or {}).get(kid)
        if key is None:
            raise jwt.InvalidTokenError("Unknown key id")
        return key

    async def _refresh_keyset(self) -> None:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(self.jwks_url)
            resp.raise_for_status()
            jwks = resp.json()
        keyset = {}
        for key in jwks.get("keys", []):
            if key.get("kty") == "RSA" and key.get("use", "sig") == "sig" and key.get("kid"):
                keyset[key["kid"]] = RSAAlgorithm.from_jwk(key)
        self._keyset = keyset
```

In `dispatch`, replace the decode block (lines ~132-134) with:

```python
        token = auth_header.removeprefix("Bearer ")
        try:
            key = await self._key_for_token(token)
            payload = jwt.decode(token, key, algorithms=[self.algorithm], audience=self.audience)
```

(Leave the existing `except jwt.ExpiredSignatureError / except jwt.InvalidTokenError / except Exception` handlers unchanged.)

- [ ] **Step 4: Run tests**

Run: `cd sdk && uv run pytest tests/test_middleware.py -v`
Expected: PASS (new rotation class passes; existing static-key tests still pass)

- [ ] **Step 5: Commit**

```bash
git add sdk/src/duar_auth/middleware.py sdk/tests/test_middleware.py
git commit -m "feat(sdk): JWTAuthMiddleware resolves key by kid, refetches JWKS on miss

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: SDK `AuthzMiddleware` + `Duar` keyset for the authz token

**Files:**
- Modify: `sdk/src/duar_auth/duar.py` (`__init__`, add `fetch_duar_keyset`, lifespan call)
- Modify: `sdk/src/duar_auth/authz_middleware.py` (resolve duar-token key by kid)
- Test: `sdk/tests/test_authz_middleware.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `sdk/tests/test_authz_middleware.py` (mirror its existing fixtures for IdP + duar keys; this test gives the middleware a `duar_instance` whose keyset is pre-seeded):

```python
class _FakeDuar:
    def __init__(self, keyset):
        self._duar_keyset = keyset
        self.idp_public_key = None
        self.idp_jwks_url = None

    @property
    def duar_keyset(self):
        return self._duar_keyset

    async def fetch_duar_keyset(self):
        return self._duar_keyset


def test_authz_token_selected_by_kid(idp_keypair, duar_keypair, make_idp_token, make_authz_token):
    # make_authz_token signs the Duar authz token WITH headers={"kid": "s1"}.
    from jwt.algorithms import RSAAlgorithm
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    _, duar_pub = duar_keypair
    keyset = {"s1": load_pem_public_key(duar_pub.encode())}
    # build app with AuthzMiddleware(duar_instance=_FakeDuar(keyset), idp_public_key=...)
    # assert a request with a kid-stamped authz token + matching idp token → 200
    ...
```

> The exact app/fixture wiring follows the existing `test_authz_middleware.py` patterns; the assertion is: a `kid`-stamped authz token verifies via the keyset, and an unknown `kid` triggers `fetch_duar_keyset` then succeeds/420s appropriately.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sdk && uv run pytest tests/test_authz_middleware.py -k kid -v`
Expected: FAIL (middleware uses a single pinned `duar_public_key`)

- [ ] **Step 3: Add keyset to `Duar`**

In `duar.py __init__`, after `self._duar_public_key: str | None = None` add:

```python
        self._duar_keyset: dict | None = None
```

Add property + fetch:

```python
    @property
    def duar_keyset(self) -> dict | None:
        return self._duar_keyset

    async def fetch_duar_keyset(self) -> dict:
        """Fetch Duar's full verification keyset ({kid: key}) from JWKS."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self.base_url}/.well-known/jwks.json")
            resp.raise_for_status()
            jwks = resp.json()
        keyset = {}
        for k in jwks.get("keys", []):
            if k.get("kty") == "RSA" and k.get("kid"):
                keyset[k["kid"]] = RSAAlgorithm.from_jwk(k)
        if not keyset:
            raise RuntimeError("No keys found in Duar JWKS response")
        self._duar_keyset = keyset
        return keyset
```

In `lifespan` `_lifespan`, replace `await self.fetch_duar_public_key()` with `await self.fetch_duar_keyset()`.

- [ ] **Step 4: Resolve the authz-token key by `kid` in `AuthzMiddleware`**

In `authz_middleware.py`, add a helper and use it in `dispatch` step 4. Insert:

```python
    async def _decode_authz(self, token: str) -> dict:
        if self._duar_public_key:  # static/air-gapped pinned key
            return jwt.decode(
                token, self._duar_public_key,
                algorithms=[self.duar_algorithm], audience=self.duar_audience,
            )
        kid = jwt.get_unverified_header(token).get("kid")
        keyset = self._duar_instance.duar_keyset if self._duar_instance else None
        if (not keyset or kid not in keyset) and self._duar_instance:
            keyset = await self._duar_instance.fetch_duar_keyset()
        key = (keyset or {}).get(kid) if kid else None
        if key is None:
            raise jwt.InvalidTokenError("Unknown authz key id")
        return jwt.decode(
            token, key, algorithms=[self.duar_algorithm], audience=self.duar_audience,
        )
```

Replace the step-4 block in `dispatch` (the `jwt.decode(authz_token, self.duar_public_key, ...)` call, lines ~153-159) with:

```python
        try:
            authz_payload = await self._decode_authz(authz_token)
        except jwt.ExpiredSignatureError:
            return JSONResponse(status_code=401, content={"detail": "Authz token expired"})
        except jwt.InvalidTokenError:
            return JSONResponse(status_code=401, content={"detail": "Invalid authz token"})
```

(Note: the `duar_public_key` constructor arg / property still exists for static mode; the `__init__` validation requiring `duar_public_key OR duar_instance` is unchanged and now satisfied by the keyset path.)

- [ ] **Step 5: Run tests**

Run: `cd sdk && uv run pytest tests/test_authz_middleware.py -v`
Expected: PASS (existing static-key tests + new kid test)

- [ ] **Step 6: Commit**

```bash
git add sdk/src/duar_auth/duar.py sdk/src/duar_auth/authz_middleware.py sdk/tests/test_authz_middleware.py
git commit -m "feat(sdk): AuthzMiddleware resolves authz-token key by kid via Duar keyset

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Rotation runbook + config example + client-upgrade note

**Files:**
- Create: `docs/deployment/key-rotation.md`
- Modify: `.env.prod.example` (add the new var with a comment)
- Modify: `docs/security.md` (link the runbook under key management)

- [ ] **Step 1: Write the runbook**

Create `docs/deployment/key-rotation.md` with: planned-rotation steps (generate keypair → move old public into `JWT_PREVIOUS_PUBLIC_KEY_PATHS` → point `JWT_PRIVATE_KEY_PATH`/`JWT_PUBLIC_KEY_PATH` at the new key → reload → keep old key ≥ `refresh_token_expire_days` (7d) → remove), the emergency/compromise variant (rotate now + revoke outstanding tokens, do NOT retain the compromised key), and the **client-upgrade note** (bump the two dev client apps to the SDK version with `kid`-aware keyset and redeploy; their users re-login once; no old-SDK support needed while all clients are in dev).

- [ ] **Step 2: Add the config example**

In `.env.prod.example`, near the JWT key paths, add:

```bash
# Retired public keys still inside their verification window (comma-separated).
# During rotation, move the previous JWT_PUBLIC_KEY_PATH here and keep it for at
# least REFRESH_TOKEN_EXPIRE_DAYS, then remove it. See docs/deployment/key-rotation.md
JWT_PREVIOUS_PUBLIC_KEY_PATHS=
```

- [ ] **Step 3: Link from security docs**

In `docs/security.md`, under the token/key section, add a line linking to `docs/deployment/key-rotation.md` and noting tokens carry a `kid` and JWKS publishes current + retired keys.

- [ ] **Step 4: Commit**

```bash
git add docs/deployment/key-rotation.md .env.prod.example docs/security.md
git commit -m "docs: JWT key-rotation runbook + client-upgrade note (MED-6)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Final verification

- [ ] `cd service && uv run pytest -q` — all green
- [ ] `cd sdk && uv run pytest -q` — all green
- [ ] `cd service && uv run ruff check . && cd ../sdk && uv run ruff check .` (or `make lint`)
- [ ] Manual smoke (optional): start the service, `curl /.well-known/jwks.json` shows one key now and two during a simulated rotation; a freshly minted token's header `kid` matches the JWKS key.

## Self-review notes
- **Spec coverage:** config (T1), provider seam (T2), kid signing + strict decode (T3), multi-key JWKS (T4), rotation continuity (T5), SDK JWTAuthMiddleware (T6), SDK AuthzMiddleware + Duar keyset (T7), runbook + client-upgrade note (T8). All spec sections mapped.
- **Strict-kid** enforced server-side (T3) and SDK JWKS mode (T6/T7); static `public_key` mode intentionally pins one key (air-gapped) and is documented as not rotation-capable.
- **Type consistency:** `signing_key() -> (pem, kid)`, `verification_keys() -> {kid: pem}`, `thumbprint_kid(pem) -> str`, `reset_cache()`, `fetch_duar_keyset() -> {kid: key}`, `_key_for_token`/`_refresh_keyset`/`_decode_authz` used consistently across tasks.
