# AuthZ-mode auto-resolve (scripts call app APIs with one token) — Design

> Approved 2026-08-22. In dual-token (AuthZ) mode an app API today needs
> `Authorization: Bearer <idp_token>` **and** `X-Authz-Token`, which makes the
> API awkward to call from scripts, Postman, or Swagger. This change lets the
> app's SDK middleware mint the authz token itself when a caller supplies only
> the IdP token plus a target workspace. SDK-only; the Duar service is untouched.

## Decisions (from brainstorm)

- **Scope:** both SDKs — Python `AuthzMiddleware` (`sdk/`) and Next.js
  `createDuarAuthzMiddleware` (`sdks/nextjs/`). Browser clients unchanged.
- **Opt-in, default off.** `auto_resolve=False` / `autoResolve: false` leaves
  every existing code path byte-identical.
- **Workspace comes from a header**, `X-Workspace-Id: <uuid>`. No discovery
  fallback, no "first workspace" auto-pick: missing both tokens → deny.
- **`X-Authz-Token` wins** when both headers are present; `X-Workspace-Id` is
  ignored on that path.
- **Provider is config**, not inferred: `idp_provider` / `idpProvider`
  (`"google"`, `"entra_id"`). Apps are already single-IdP (one JWKS, one issuer).
- **Cache keyed by `(idp_sub, workspace_id)`** — not by token hash — so a script
  that fetches a fresh IdP token per request still costs one mint per TTL.
  Safe because the IdP token is signature/aud/iss-verified on every request
  *before* the cache lookup, and the `idp_sub` binding check still runs.
- **Single-flight** on cache miss: concurrent misses for one key share one mint.
  Non-negotiable — `/authz/resolve` is rate-limited 60/min **per calling
  service**, a bucket shared with the browser mint path.
- **No Duar service changes.** No GitHub (opaque token) support — both
  middlewares already require a JWT IdP token. No minted-token echo in a
  response header.
- **IdP audience is assumed solved** (the app's `idp_audience` /
  `allowed_idp_audiences` include whatever client id the script's token carries).

## Why this is not a security regression

The trust boundary does not move. Every app already exposes a mint endpoint
(`POST /api/duar/authz/resolve` via the SDK proxy router, or its own) that
accepts any valid IdP token and returns an authz token using the service key it
holds. A script can already do `mint → call API` in two requests; this
collapses it into one. Still enforced on the auto path:

- Duar's mint gate (`X-Service-Key` required; origin-auth cannot mint).
- IdP `aud`/`iss` pinned locally in the middleware and in Duar's
  `allowed_idp_audiences`; a token for app A cannot mint at app B.
- `svc` binding — minted `svc` is the key's `effective_scope`, compared against
  the middleware's own `effective_scope` (realms work with no extra wiring).
- Org gate, membership, `is_active` — all run inside `/authz/resolve` per mint.
- Backend authorization stays on Duar-signed `wrole`/`actions`, never on the bare
  IdP token.
- No CSRF surface: the auto path requires `Authorization: Bearer`, which
  browsers never attach ambiently; no cookies involved.
- Cache poisoning: values only arrive over the service-key channel, and a
  request must prove the same `idp_sub` locally before it can hit an entry.
- Revocation window: ≤ authz TTL (5 min default) via cache — identical to the
  browser flow today, already documented as "offline by design".

Lost: the login `nonce` on the mint (a script has no login session). Bounded
by the IdP token's own `exp`; a stolen IdP token already grants access either
way.

## 1. App-facing contract

| Request headers | Result |
|---|---|
| `Authorization: Bearer <idp>` + `X-Authz-Token` | existing path, unchanged; `X-Workspace-Id` ignored if also sent |
| `Authorization` + `X-Workspace-Id: <uuid>` | auto-resolve → same `request.state` (Python) / `x-duar-*` headers (Next.js) as the existing path |
| `Authorization` only | `401 {"detail": "Missing authz token — send X-Authz-Token, or X-Workspace-Id to resolve one server-side"}` |
| `X-Workspace-Id` not a UUID | `400 {"detail": "Invalid X-Workspace-Id"}` — before any Duar call |

Mint outcome mapping (Duar status → app response):

| Duar | App | Detail |
|---|---|---|
| 400 (IdP token rejected) | 401 | `IdP token rejected by Duar` |
| 403 (not member / org not allowed / inactive) | 403 | `Not authorized for this workspace` |
| 409 (cross-provider email conflict) | 403 | `Not authorized for this workspace` |
| 429 | 429 | `Authorization service rate limit` + `Retry-After` passed through when Duar sent one |
| other non-200, network error, timeout | 503 | `Authorization service unavailable` |

Success sets exactly what the existing path sets: Python `request.state.user`,
`request.state.token` (the minted authz token — so `RequestAuth.can()` /
`check_action()` work unchanged via `dependencies.get_token`),
`request.state.idp_token`. Next.js sets the `x-duar-*` headers **and**
`x-authz-token` on the forwarded request so server code reading it keeps
working.

## 2. Configuration

**Python**

```python
Duar(
    ...,
    idp_provider="google",   # new; required when auto_resolve=True
    auto_resolve=True,       # new; default False
)
duar.protect(app)            # passes both through to AuthzMiddleware
```

`AuthzMiddleware(..., idp_provider: str | None = None, auto_resolve: bool = False)`.
Constructor raises `ValueError` if `auto_resolve` is set without
`duar_instance` (static-key / air-gapped mode has no service key) or without
`idp_provider`.

`DuarError` gains `retry_after: str | None = None` (backward-compatible kwarg).
`AuthzClient.resolve` populates it from the response `Retry-After` header on
non-200.

**Next.js**

```ts
createDuarAuthzMiddleware({
  ...,
  autoResolve: true,                       // new; default false
  serviceKey: process.env.DUAR_SERVICE_KEY!, // new; server-only env
  idpProvider: 'google',                   // new
})
```

Throws at creation if `autoResolve` is set without `serviceKey` or `idpProvider`.

## 3. Cache and single-flight

- Key: `` `${idp_sub}|${workspace_id}` `` → `{ token, expiresAt }`.
- Expiry: **80% of `expires_in`** from the resolve response (same convention as
  `M2mTokenClient`), so a cached token is never handed out seconds before it
  expires.
- Bound: 4096 entries, oldest evicted. Python `OrderedDict` on the middleware
  instance; Next.js `Map` inside the `createDuarAuthzMiddleware` closure (not
  module scope — keeps test instances isolated).
- Single-flight: a pending `asyncio.Future` / `Promise` per key; concurrent
  misses await the same mint. The pending entry is removed when it settles,
  success or failure. Failures are not cached.
- Only locally-verified IdP tokens reach the cache, so it cannot be filled with
  junk; the bound is a backstop.

## 4. Dispatch order

**`auto_resolve=False`:** the existing code path runs untouched, including the
early `"Missing authz token"` 401 before IdP validation. Existing tests stay
byte-identical.

**`auto_resolve=True`:**

1. Extract `Authorization` (401 if missing) and `X-Authz-Token` (may be absent).
2. Validate the IdP token locally — unchanged step — so junk never consumes the
   Duar rate bucket.
3. If `X-Authz-Token` is absent:
   - read `X-Workspace-Id`; absent → 401 (contract table); not a UUID → 400;
   - cache lookup by `(idp_payload["sub"], workspace_id)`;
   - miss → `duar_instance.authz.resolve(idp_token, idp_provider, workspace_id)`
     under single-flight; map errors per §1; store `(authz_token, 0.8 × expires_in)`.
4. Continue into the unchanged authz-token validation, `idp_sub` binding, `svc`
   binding, and `request.state` population with the resolved token.

Next.js mirrors this inside the existing `try` block: verify the IdP token
first (was a parallel `Promise.all` with the authz verify — becomes sequential
only on the auto path), then resolve, then the unchanged authz verify + binding
checks + header forwarding. Page navigations (no `Authorization` header) keep
redirecting to `loginPath` exactly as today; the contract-table JSON bodies
apply to `/api/*` routes, where `handleUnauthenticated` already returns JSON.

## 5. Testing

**Python** — `sdk/tests/test_authz_middleware.py`, new `_FakeAutoDuar` fake
exposing `.authz.resolve` (the existing `_FakeDuar` is frozen; leave it):

- missing both tokens → 401 with the contract detail
- `X-Workspace-Id` present → resolve called once with `(idp_token, provider, wid)`; `request.state.user` populated; `request.state.token` is the minted token
- second request same user/workspace → cache hit, resolve still called once
- N concurrent first requests → resolve called once (single-flight)
- both headers present → resolve never called
- bad UUID → 400, resolve never called
- Duar 403 → 403; 429 with `Retry-After` → 429 + header; network error → 503
- `auto_resolve=False` → existing `"Missing authz token"` behaviour
- constructor guards (`auto_resolve` without `duar_instance`; without `idp_provider`)

**Next.js** — new `sdks/nextjs/src/__tests__/authz-middleware.test.ts`
(vitest, `vi.stubGlobal('fetch')`, jose-generated RSA keys served as JWKS
through the fetch mock for both the IdP and Duar JWKS URLs): the same cases
minus concurrency, plus "forwarded request carries `x-authz-token`".

## 6. Docs and changelog

- `docs/sdk/middleware.md`, `docs/sdk/duar-class.md`: config reference + a
  "Calling the API from scripts" section with one `curl`.
- `docs/js-sdk/server.md` (Next.js middleware section): the same.
- `docs/guide/how-it-works.md`: one paragraph under the dual-token explanation.
- `CHANGELOG.md` → `[Unreleased] / Added`. No version bump in this work.

## 7. Non-goals

Duar service changes · GitHub (opaque token) support · browser-client changes ·
echoing the minted token in a response header · workspace discovery fallback ·
forwarding script IPs to Duar's audit (`X-Forwarded-For` on the resolve call).
