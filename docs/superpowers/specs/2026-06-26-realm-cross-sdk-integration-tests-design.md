# Cross-SDK Realm Integration Tests — Design

**Date:** 2026-06-26
**Status:** Approved (design)
**Branch:** `realm-trusted-app-group`
**Task:** Add integration tests that exercise the **real** Duar token minters against the **real** SDK verifiers — in Python and JS — to prove realm Flow A (shared user-context) and Flow B (no-user m2m) hold end-to-end across a Python app and a JS app in one realm. Deferred from Plans 5–6 (the unit suites are mock-only).

## Problem — what the existing suites do NOT cover

The shipped unit tests are deliberately isolated and leave two real gaps:

1. **Claim drift between service and SDK is undetected.** The Python `test_m2m_verify.py` hand-rolls its own m2m token with `pyjwt.encode` — it never imports the service's real `create_m2m_token`. So if the service's minter changes a claim (renames `caller`, drops `aud_target`, changes `aud`), the SDK verifier unit test keeps passing against the *stale hand-rolled shape*. Nothing asserts the two codebases still agree on the wire.
2. **JS verification has never run against real crypto.** `sdks/js/src/__tests__/m2m.test.ts` does `vi.mock('jose', ...)` — `jwtVerify` is a stub. The real `jose` RS256 path, JWKS-by-`kid` resolution, and `aud`/`exp` enforcement in `verifyM2mToken` are never executed.

The value of this work is closing exactly those two gaps — **not** re-testing HTTP plumbing (`fetch_whoami`/`mint_m2m_token` round-trips are already respx/fetch-mock covered).

## Goals

- A **real service-minted** authz token (svc = realm slug) is accepted by the **real** Python `AuthzMiddleware` and rejected when its `svc` is a different scope (Flow A contract).
- A **real service-minted** m2m token (real `/realm/m2m-token`) is accepted by the **real** Python `verify_m2m_token` → `SystemAuth`, and rejected for wrong-realm / expired / wrong-audience / non-member (Flow B contract).
- The **same token bytes** are accepted by the **real** (un-mocked) JS `verifyM2mToken`, proving Python-app ↔ JS-app realm interop.
- Catch service↔SDK claim drift: the Python side mints **live** through the real endpoint/minter; a committed fixture ties the JS side to the same shape with a freshness guard.

## Non-Goals

- **No two-process / live-listener e2e** (no uvicorn, no real ports, no Postgres/Redis). Rejected in brainstorming as ops-heavy and flaky in the network sandbox.
- **No re-testing of the SDK's own HTTP client** (`fetch_whoami`, `mint_m2m_token`, `M2mTokenClient.getToken`) — already covered by respx (Python) and fetch-mock (JS) unit tests. The SDK's HTTP client cannot take an injected transport, and a loopback uvicorn fixture would add flakiness for no new coverage.
- **No `nextjs` test** — `@duar-auth/nextjs` has no unit harness and only **re-exports** `@duar-auth/js`'s m2m helpers, which this design covers at the source.
- No exercise of the DB-heavy `/authz/resolve` endpoint (it has its own tests); Flow A's authz token is produced by calling the real `create_authz_token` minter directly.

## Architecture — three pieces

### 1. `gen_fixtures.py` — the cross-language artifact (service env)

A standalone script (not run under pytest) that produces the committed `fixtures.json` the JS test consumes. It:

1. Sets `JWT_PRIVATE_KEY_PATH` / `JWT_PUBLIC_KEY_PATH` (→ the committed test-only keypair) and `BASE_URL` **before** importing any `src.*` module, so the service's `key_provider` signs with a deterministic, committed key (the minters sign via `key_provider.signing_key()` — `jwt.py:_sign`).
2. Calls the **real** `src.auth.jwt.create_authz_token` and `create_m2m_token` to mint a labelled set of tokens, and `src.auth.jwks.build_jwks()` for the matching JWKS.
3. Writes `fixtures.json`: `{ "jwks": {...}, "public_pem": "...", "issuer": "...", "tokens": { <label>: "<jwt>" } }`.

Token set (labels):

| label | minted with | purpose |
|---|---|---|
| `m2m_valid` | `create_m2m_token(svc="acme-suite", caller="app-a", ttl_s=<10 years>)` | far-future `exp` so the committed vector never rots |
| `m2m_expired` | `create_m2m_token(..., ttl_s=-10)` | past `exp` → reject |
| `m2m_wrong_realm` | `create_m2m_token(svc="other-realm", ...)` | svc ≠ receiver scope → reject |
| `m2m_aud_target` | `create_m2m_token(..., aud_target="billing")` | targeted token → reject for a different receiver |
| `authz_valid` | `create_authz_token(service_name="acme-suite", ...)` | Flow A token, svc = realm slug; **also** the wrong-audience negative |

The "wrong audience" negative reuses `authz_valid` (aud = `sentinel:authz`): fed to the m2m verifier it must reject (token-type-confusion defense). So the fixture holds **five** tokens, not six — no separate `wrong_aud` entry.

Regenerated via a `make` target (e.g. `make realm-fixtures`) when the token shape changes. The script is the single source of the committed bytes.

### 2. Python integration test — in-process, live (`service/tests/integration/`)

`test_realm_flows.py` — pure in-process (TestClient = httpx against ASGI, **no socket**), reusing the `test_realm_routes.py` override pattern (override `require_service_key` → `ServiceKeyContext(service_name=..., realm_slug=...)`, `get_db` → yields `None`, monkeypatch `realm_service.get_realm_by_slug` → a fake `_Realm`, disable the limiter).

- **Flow B (live):** `TestClient.post("/realm/m2m-token")` drives the **real** handler → a **real** m2m token. The test reads the token's `kid` header and pulls the matching public PEM from `key_provider.verification_keys()[kid]` — so it feeds the SDK the actual signing key, with **no env/keypair wrangling**. It constructs the real SDK `Duar`, sets `_duar_public_key` = that PEM and `_effective_scope = "acme-suite"`, then asserts `verify_m2m_token(token)` → `SystemAuth(caller="…", svc="acme-suite", actions=["*"])`.
  - Negatives: wrong-realm (`_effective_scope` mismatch), expired (`ttl_s=-10` via the minter), non-member (mint returns 403), wrong-aud (an authz token rejected by `verify_m2m_token`), `aud_target` mismatch.
- **Flow A (contract):** call the real `create_authz_token(service_name="acme-suite", …)` for the authz token + a fake IdP token signed by an in-test IdP keypair (mirrors `test_authz_effective_scope.py`'s two-keypair `TestClient` setup). The real `AuthzMiddleware` (static-key mode, `duar_public_key` = the signing key's public PEM via `key_provider`, `idp_public_key` = the test IdP pub, `effective_scope` via the `_FakeInstance` stub) accepts it; a token minted with `service_name="other"` is rejected (403).
- **Fixture freshness guard:** load the committed `fixtures.json`, feed its `public_pem` to the SDK, and assert `m2m_valid` verifies to a `SystemAuth` while `m2m_expired`/`m2m_wrong_realm`/`m2m_aud_target`/`wrong_aud` each reject. If the service minter changes shape and `fixtures.json` is not regenerated, this fails — protecting the JS fixture from silent staleness. (One test file doubles as the guard; no separate mechanism.)

### 3. JS integration test — real crypto (`sdks/js/src/__tests__/realm-integration.test.ts`)

A **separate** test file that does **not** `vi.mock('jose')` — real `jose`. It:

1. Imports the committed `fixtures.json` (relative import from the repo, e.g. `../../../../service/tests/integration/fixtures/fixtures.json`).
2. Stubs `global.fetch` to return the fixture `jwks` (so `createRemoteJWKSet` resolves the real key by `kid` without a network call — only the key *transport* is stubbed; verification crypto is real).
3. Real `verifyM2mToken(tokens.m2m_valid, { jwksUrl, effectiveScope: "acme-suite" })` → asserts `caller`, `svc`, `can()`. Each negative (`m2m_expired`, `m2m_wrong_realm`, `m2m_aud_target` with `serviceName: "reports"`, `wrong_aud`) rejects.

## Key/issuer handling (the one subtlety)

- The minters sign via `key_provider.signing_key() → (private_pem, kid)` and stamp `iss = settings.base_url`. `_ISSUER` and the algorithm assertion are evaluated at `jwt.py` import.
- **`gen_fixtures.py`** controls determinism by setting `JWT_*_KEY_PATH` + `BASE_URL` *before* importing `src.*`, so the committed JWKS, tokens, and `public_pem` all derive from one committed test keypair.
- **The Python test needs no such wrangling:** it never pins a keypair. For live tokens it reads `kid` → `key_provider.verification_keys()[kid]`; for the guard it uses `fixtures.json`'s own `public_pem`. Both verifiers get the correct public key regardless of which key the service test env happens to load.
- The SDK `verify_m2m_token` ignores `iss` (decodes by audience only); JS `verifyM2mToken` checks `issuer` only if passed (we don't). So `iss` value is cosmetic here. Static-key SDK verify ignores the token's `kid` header (uses the provided PEM), so a `kid`-stamped real token verifies fine in static mode.

## Env wiring

The Python integration test imports both `src.*` (service) and `duar_auth` (SDK) in one process. **Update (verified at plan time): no dependency change is needed** — the root `daikon-duar` workspace already depends on both `duar` and `duar-auth` (`[tool.uv.sources] … { workspace = true }`), so the shared workspace venv already exposes `duar_auth` in the service test run (`cd service && uv run python -c "import duar_auth, src.auth.jwt"` succeeds). The earlier "add a dev-dep" plan is a no-op and is dropped. The JS test runs in the existing `sdks/js` vitest harness; it only reads the committed JSON (no new dependency).

## Keypair handling — no committed private key

**Update (verified at plan time):** `gen_fixtures.py` generates an **ephemeral in-memory RSA keypair** each run, writes it to throwaway temp files, points `JWT_*_KEY_PATH` at them, mints, then discards them. Only `fixtures.json` (JWKS + public PEM + tokens) is committed — **no private key is ever committed** (avoids the trivy secret-scanner and the `keys/` dependency). The Python *live* tests use the ambient `key_provider` (dev `keys/`) and read the verifying key by `kid` from `key_provider.verification_keys()`; the freshness guard uses `fixtures.json`'s own `public_pem`. (Supersedes the `test_key.pem`/`test_key.pub.pem` entries in the file layout below.)

## File layout

```
service/tests/integration/
  __init__.py
  conftest.py                       # (if needed) limiter disable / shared helpers
  fixtures/
    test_key.pem  test_key.pub.pem  # committed test-only RSA keypair
    fixtures.json                   # committed: { jwks, public_pem, issuer, tokens{} }
  gen_fixtures.py                   # regenerates fixtures.json from the REAL minters
  test_realm_flows.py               # Flow A + Flow B + negatives + freshness guard
sdks/js/src/__tests__/
  realm-integration.test.ts         # jose UN-mocked; verifies the shared bytes
```

`make realm-fixtures` → runs `gen_fixtures.py` (documented; re-run when token shape changes).

## Testing / verification gates

- `cd service && uv run pytest tests/integration/` — green.
- `cd sdks/js && npm test` — green (the new file plus the existing mocked suite coexist; mocking is per-file, so the un-mocked integration file does not affect the mocked `m2m.test.ts`).
- Broad-suite IdP/JWKS connection failures remain a known network-sandbox artifact (these tests make no external network calls).

## Out of scope / future

- A loopback-uvicorn variant to also drive the SDK's real HTTP client (`mint_m2m_token`/`fetch_whoami`) end-to-end — deferred; respx/fetch-mock cover that path.
- True two-process Python↔JS live interop — deferred (heavy, flaky).
- Wiring the JS integration file into CI if/when a JS CI job is added (today gated locally via `npm test`).
