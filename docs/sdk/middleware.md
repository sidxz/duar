# Middleware

The SDK provides two middleware classes. Use `AuthzMiddleware` in authz mode (dual-token) or `JWTAuthMiddleware` in proxy mode (single JWT). If you use the `Duar` class, `protect()` adds the correct one automatically.

## AuthzMiddleware (AuthZ Mode)

Validates both an IdP token and a Duar authorization token on each request. Checks that the `sub` claims match across tokens (binding verification).

### Headers

| Header | Content |
|--------|---------|
| `Authorization` | `Bearer <idp_token>` |
| `X-Authz-Token` | `<duar_authz_token>` |

### Setup

```python
from duar_auth.authz_middleware import AuthzMiddleware

app.add_middleware(
    AuthzMiddleware,
    service_name="my-service",
    idp_audience="123-abc.apps.googleusercontent.com",
    idp_issuer="https://accounts.google.com",
    idp_jwks_url="https://www.googleapis.com/oauth2/v3/certs",
    duar_public_key=duar_pem,
    exclude_paths=["/health", "/docs", "/openapi.json"],
)
```

Or pass a `Duar` instance (keys are read lazily -- safe to call before lifespan). In that case `service_name`, `idp_audience`, and `idp_issuer` are picked up from the Duar instance:

```python
duar.protect(app)  # preferred
```

### Constructor Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `service_name` | `str` | **required** | Binds this middleware to a single service. The authz token's `svc` claim MUST equal this. Prevents a token minted for another service from being replayed here. |
| `idp_audience` | `str \| list[str]` | **required** | The IdP token's expected `aud` claim — typically your OAuth client_id. Without this check, any valid token from any OAuth client of the same IdP authenticates. |
| `idp_issuer` | `str \| None` | `None` | Expected IdP `iss` claim. Strongly recommended. |
| `idp_public_key` | `str \| None` | `None` | PEM key for IdP token validation |
| `idp_jwks_url` | `str \| None` | `None` | JWKS endpoint for IdP tokens (handles key rotation) |
| `duar_public_key` | `str \| None` | `None` | PEM key for authz token validation |
| `duar_instance` | `Duar \| None` | `None` | Duar instance (reads keys lazily) |
| `idp_algorithm` | `str` | `"RS256"` | IdP token signing algorithm |
| `duar_algorithm` | `str` | `"RS256"` | Authz token signing algorithm |
| `duar_audience` | `str` | `"duar:authz"` | Expected `aud` claim in authz token |
| `exclude_paths` | `list[str] \| None` | `["/health", "/docs", "/openapi.json"]` | Paths that bypass authentication |

Either `duar_public_key` or `duar_instance` is required. For IdP validation, the middleware uses `idp_jwks_url` or `idp_public_key` (from the params or from the Duar instance).

### Request State

After successful validation, the middleware sets:

| Attribute | Type | Description |
|-----------|------|-------------|
| `request.state.user` | `AuthenticatedUser` | User built from authz token claims + IdP email/name |
| `request.state.token` | `str` | The Duar authz token |
| `request.state.idp_token` | `str` | The original IdP token |

### Validation Steps

1. Extract IdP token from `Authorization: Bearer ...`
2. Extract authz token from `X-Authz-Token`
3. Validate IdP token: signature, expiry, `aud == idp_audience` (+ `iss == idp_issuer` if configured) via JWKS or static key
4. Validate authz token: signature, expiry, `aud == "duar:authz"`
5. Verify binding: IdP `sub` must equal authz `idp_sub` (both must be non-empty)
6. Verify service binding: authz `svc` must equal `service_name`
7. Build `AuthenticatedUser` and set on `request.state`

OPTIONS requests are passed through without validation.

---

## JWTAuthMiddleware (Proxy Mode)

Validates a single Duar-issued JWT. Used when Duar handles the full OAuth flow.

### Setup

```python
from duar_auth.middleware import JWTAuthMiddleware

# Recommended: fetch key from JWKS automatically
app.add_middleware(
    JWTAuthMiddleware,
    base_url="https://duar.example.com",
)

# Alternative: static PEM key
app.add_middleware(
    JWTAuthMiddleware,
    public_key=Path("keys/public.pem").read_text(),
)
```

### Constructor Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `base_url` | `str \| None` | `None` | Duar URL. JWKS endpoint derived as `{base_url}/.well-known/jwks.json` |
| `public_key` | `str \| None` | `None` | RSA PEM key for air-gapped deployments |
| `jwks_url` | `str \| None` | `None` | Explicit JWKS URL (non-standard paths only) |
| `algorithm` | `str` | `"RS256"` | JWT signing algorithm |
| `audience` | `str` | `"duar:access"` | Expected `aud` claim |
| `exclude_paths` | `list[str] \| None` | `["/health", "/docs", "/openapi.json"]` | Paths that bypass authentication |
| `allowed_workspaces` | `set[str] \| None` | `None` | Workspace IDs permitted. `None` allows all |

One of `base_url`, `jwks_url`, or `public_key` is required. The JWKS key is fetched lazily on first request and cached.

### Request State

| Attribute | Type | Description |
|-----------|------|-------------|
| `request.state.user` | `AuthenticatedUser` | User built from JWT claims |
| `request.state.token` | `str` | The raw JWT string |

---

## Excluded Paths

Both middleware classes skip authentication for excluded paths. Matching uses exact match or prefix with `/` boundary:

```python
# Path "/health" matches:     /health, /health/ready
# Path "/docs" matches:       /docs, /docs/oauth2-redirect
# Path "/docs" does NOT match: /documents
```

## Error Responses

Both middleware classes return JSON errors:

| Status | Detail | When |
|--------|--------|------|
| 401 | `Missing IdP token` / `Missing or invalid Authorization header` | No `Authorization: Bearer` header |
| 401 | `Missing authz token` | No `X-Authz-Token` header (authz mode only) |
| 401 | `IdP token expired` / `Token has expired` | Token `exp` in the past |
| 401 | `Invalid IdP token` / `Invalid token` | Bad signature, malformed, wrong audience |
| 401 | `Token binding mismatch: idp_sub does not match` | IdP sub != authz idp_sub (authz mode) |
| 401 | `Invalid token claims` | Missing required claims in payload |
| 403 | `Authz token was issued for a different service` | authz `svc` claim != `service_name` (authz mode) |
| 403 | `Workspace not permitted for this service` | Workspace not in `allowed_workspaces` |
| 500 | `Authentication service unavailable` | JWKS fetch failed (proxy mode) |
