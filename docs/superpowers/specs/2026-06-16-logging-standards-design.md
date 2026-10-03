# Logging Standards — Commercial-Grade Structured Observability

- **Date:** 2026-06-16
- **Status:** Approved (design) — pending spec review → implementation plan
- **Scope:** `service/` backend (primary) + `admin/` React SPA (client logging add-on)
- **Author:** brainstormed with Siddhant

## 1. Problem

Duar is a commercial-grade authentication proxy + authorization microservice. Its
logging is not commercial-grade. `structlog>=24.4.0` is a dependency but **never
configured**, so today the service emits plain-text lines to stdout with structlog's
defaults: no JSON, no central config, no request correlation, no access logs, and
security-relevant events are scattered and inconsistent. Rate-limit rejections (429s)
are emitted **silently**. Raw emails are logged in two places.

The logs are not good enough to (a) operate the service, (b) investigate incidents, or
(c) feed an AI pipeline for failure detection, usage-pattern analysis, and anomaly
detection. This effort standardizes logging across the app to fix all three.

## 2. Current state (investigation findings)

| Area | Today |
|------|-------|
| Library | `structlog` imported, **unconfigured** → plain text to stdout |
| Call sites | 35 across 6 files; already event-name + kwargs style (good base) |
| Config | None — no `LOG_LEVEL`, no format/handler/init |
| Correlation | None — no request ID, no contextvars binding |
| Access logging | None — no method/path/status/latency record |
| Rate-limit hits | **Silent** — 429s produce no log line |
| Audit | Split-brain: admin actions → DB `ActivityLog` table; auth failures → structlog |
| PII | Raw `email` logged at `authz_routes.py:259` and `:267`; UUIDs in several warnings. No tokens/secrets logged. |
| Deployment | Orca Docker swarm → stdout collection is the natural transport |

Confirmed code facts:
- Middleware execution order (outermost→innermost), from `main.py:157-187`:
  `DynamicCORS → TrustedHost → Session → SecurityHeaders → GlobalRateLimit → MaxBodySize → app`
  (`add_middleware` adds to the top of the stack, so **last-added is outermost**).
- Rate limiting is two mechanisms: `GlobalRateLimitMiddleware` + a slowapi
  `RateLimitExceeded` exception handler (`rate_limit_exceeded_handler`).
- Config is `pydantic-settings` `BaseSettings` with `@property` list parsers (`config.py`).
- `activity_service.log_activity(db, action, target_type, target_id, actor_id,
  workspace_id, detail)` is the single choke point for DB audit writes.
- `src/version.py` exposes `__version__` (currently `0.12.0`).

## 3. Decisions

| # | Decision | Choice |
|---|----------|--------|
| D1 | Log sink | **Vendor-neutral JSON to stdout** → swarm log driver → aggregator (Loki/ELK/Datadog/CloudWatch/Splunk all consume it) |
| D2 | PII policy | **Redact emails, keep UUIDs.** Never log raw email/name; keep user UUID (already pseudonymous) for correlation; keep `email_domain` for tenancy signals |
| D3 | Scope | **Backend + admin SPA** |
| D4 | Depth | **Full observability, no distributed tracing:** structured config + request-ID correlation + access logging + security/audit event taxonomy + rate-limit/auth coverage + PII redaction + retrofit |
| D5 | Admin SPA logging | **Full client→backend ingest path** (not console-only) — client ships security-relevant events to a backend endpoint that re-emits them into the same JSON stream |
| D6 | Pipeline approach | **Single structlog pipeline; route stdlib logging through it** via `ProcessorFormatter` so app + framework (uvicorn/sqlalchemy/authlib/slowapi) logs share one JSON schema. Zero new runtime deps. |

## 4. Goals / Non-goals

**Goals**
- One consistent, machine-parseable JSON line schema across the whole service.
- Every log line within a request is correlatable via a stable `request_id`.
- Every security-relevant event (auth, authz, rate limit, admin, permission) is emitted
  with a stable `event` name, `outcome`, and machine `reason` code.
- No PII (raw emails/names) and no secrets ever reach the log stream.
- The schema is sufficient for an AI pipeline to baseline rates and flag anomalies.
- Admin SPA security signals (failed logins, repeated 401/403, uncaught errors) reach the
  same stream.

**Non-goals**
- Distributed tracing / OpenTelemetry (explicitly deferred — D4).
- Metrics/Prometheus endpoints (separate concern).
- Replacing the DB `ActivityLog` table (kept for compliance queries; we *add* a log stream).
- Log aggregator/dashboard provisioning (out of repo scope).

## 5. The canonical log envelope (AI-ready schema)

Every line carries this stable base. Event-specific fields are added on top, always
PII-redacted.

```jsonc
{
  "ts": "2026-06-16T12:34:56.789Z",   // ISO8601 UTC, always present
  "level": "warning",                  // debug|info|warning|error|critical
  "event": "authz.token.denied",       // STABLE dotted name: domain.object.action
  "category": "security",              // access | security | audit | app
  "outcome": "denied",                 // success | failure | denied | error  (omit for pure info)
  "reason": "not_member",              // machine code from a finite vocabulary (optional)
  "service": "duar",               // constant
  "version": "0.12.0",                 // from src/version.__version__
  "env": "prod",                       // derived from settings.debug
  "request_id": "01J9...",             // ULID; correlates all lines in a request
  "actor": "u_3f9a2c..",               // user UUID or "anonymous"
  "workspace_id": "ws_..",             // when known
  "caller_service": "docu-store",      // service_name of the calling service key, when known
  "source_ip": "203.0.113.4"           // rightmost-hop, respects trusted_proxy_count
}
```

Rules:
- `event` names are a **closed, documented vocabulary** (section 6). New events are added
  to the vocabulary, not invented ad hoc. This is what makes the stream learnable.
- `category` partitions the stream for routing/alerting: `access` (one per request),
  `security` (auth/authz/abuse), `audit` (state-changing admin/business actions),
  `app` (everything else — startup, background, errors).
- `outcome` + `reason` are the primary anomaly-detection dimensions. `reason` codes are
  `snake_case`, finite, and stable.

## 6. Event taxonomy (vocabulary)

Dotted `domain.object.action`. Representative set (implementation may extend the table,
documented in `docs/`):

| Event | category | typical outcome | key fields |
|-------|----------|-----------------|------------|
| `http.access` | access | success/failure/error | `http.method`, `http.route`, `http.status`, `duration_ms`, `resp_bytes` |
| `auth.login.succeeded` | security | success | `provider`, `actor` |
| `auth.login.failed` | security | failure | `provider`, `reason` |
| `auth.token.issued` | security | success | `token_type`, `actor` |
| `auth.token.refreshed` | security | success | `actor` |
| `auth.token.reuse_detected` | security | failure | `actor`, `jti` (hashed) — **hard compromise signal** |
| `auth.token.revoked` | security | success | `actor`, `count` |
| `authz.token.issued` | security | success | `caller_service`, `workspace_role`, `actions_count` |
| `authz.token.denied` | security | denied | `reason` (`not_member`/`inactive_user`/`email_conflict`/`org_not_allowed`/`org_not_permitted`), `caller_service` |
| `authz.idp.validation_failed` | security | failure | `provider`, `reason` |
| `ratelimit.exceeded` | security | denied | `limit`, `http.route`, `source_ip` — **was silent** |
| `permission.registered` | audit | success | `caller_service`, `resource_type`, `resource_id` |
| `permission.share.granted` / `.revoked` | audit | success | `resource_type`, `resource_id`, `grantee` |
| `permission.visibility.updated` | audit | success | `resource_type`, `resource_id` |
| `permission.deregistered` | audit | success | `resource_type`, `resource_id` |
| `admin.action` | audit | success | `action`, `target_type`, `target_id` |
| `audit.activity` | audit | success | `action`, `target_type`, `target_id`, `actor`, `workspace_id` (dual-emit from `log_activity`) |
| `app.startup` / `app.shutdown` | app | — | `port` / — |
| `app.config.insecure` | app | failure | `reason` (replaces bare `logger.critical(e)`) |
| `app.error.unhandled` | app | error | `error.type`, `error` (+ `exc_info`) |
| `client.*` | security/app | — | forwarded admin-SPA events (section 7.9), prefixed `client.` |

## 7. Architecture / components

### 7.1 `service/src/logging_config.py` (new)

`configure_logging(settings)` — called once at the **top** of the `lifespan` startup in
`main.py`, before any log line. Processor chain (shared by structlog + stdlib via
`ProcessorFormatter`):

1. `structlog.contextvars.merge_contextvars` — pulls in `request_id`/`actor`/etc.
2. `structlog.processors.add_log_level`
3. ISO8601 UTC timestamp (`ts`)
4. static-field injector — `service`, `version` (from `__version__`), `env`
5. **PII redaction processor** (section 7.4)
6. `structlog.processors.StackInfoRenderer` + `format_exc_info`
7. renderer: `JSONRenderer` when `LOG_FORMAT=json`, else `ConsoleRenderer` (dev)

stdlib bridge: root logger gets a single `StreamHandler(stdout)` whose formatter is
`structlog.stdlib.ProcessorFormatter` using the same chain, so uvicorn/sqlalchemy/authlib/
slowapi lines render identically. Tame noise: disable `uvicorn.access` (we own access
logs), set `sqlalchemy.engine`→WARNING, `uvicorn.error` passes through.

### 7.2 Config flags (`service/src/config.py`)

Add to `Settings`:
- `log_level: str = "INFO"`
- `log_format: str = "json"` (dev `.env` sets `LOG_FORMAT=console`)
- `log_pii_redaction: bool = True`
- `@property environment` → `"dev" if self.debug else "prod"` (used as `env` field)

`SERVICE_VERSION` is read from `src/version.__version__`, not a config flag.

### 7.3 Request correlation + access logging — `service/src/middleware/request_context.py` (new)

Two cooperating middlewares, **added after `DynamicCORSMiddleware`** so they sit
outermost (request_id is bound before any other middleware/handler runs, and the access
log captures the final status — including 429s and TrustedHost rejections):

```python
app.add_middleware(DynamicCORSMiddleware)          # existing (was outermost)
app.add_middleware(AccessLogMiddleware)             # NEW
app.add_middleware(RequestContextMiddleware)        # NEW — last added = outermost
```

Resulting order (outermost→innermost):
`RequestContext → AccessLog → DynamicCORS → TrustedHost → Session → SecurityHeaders → GlobalRateLimit → MaxBodySize → app`.

- **RequestContextMiddleware**: read inbound `X-Request-ID` (validate as ULID/UUID, else
  ignore), else mint a ULID. `bind_contextvars(request_id=…)`; set `request.state.request_id`;
  echo `X-Request-ID` in the response; `clear_contextvars()` in a `finally`. Auth
  dependencies enrich context (`bind_contextvars(actor=…, workspace_id=…, caller_service=…)`)
  as identity resolves.
- **AccessLogMiddleware**: time the request; emit exactly **one** `http.access` event on
  the way out — `http.method`, `http.route` (route **template** from the matched route, not
  the raw path, to avoid PII-in-URL and high cardinality; fall back to raw path when no
  route matched), `http.status`, `duration_ms`, `source_ip` (reuse `trusted_proxy_count`
  rightmost-hop logic from rate_limit), `resp_bytes`, user agent. Level from status:
  `<400`→INFO, `4xx`→WARNING, `5xx`→ERROR. Skip list (configurable): `/health`, `/docs`,
  `/redoc`, `/openapi.json`, `/.well-known/jwks.json`.

ULID: use a tiny dependency-free generator or `uuid4().hex` if a ULID lib is undesirable
(decide in plan; default to `uuid4` to avoid a new dep, formatted without dashes).

### 7.4 PII redaction processor

structlog processor that recursively walks the event dict and masks a **denylist** of keys.
**Matching semantics:** exact key match (case-insensitive) for the denylist, plus a single
suffix pattern `*_email`. Exact-match means `name` is redacted but `service_name`,
`workspace_id`, `caller_service`, `actor` are **not** — avoiding accidental over-redaction
of envelope/dimension fields. Denylist: `password`, `token`, `access_token`,
`refresh_token`, `id_token`, `authorization`, `cookie`, `set-cookie`, `service_key`,
`api_key`, `client_secret`, `secret`, `jwt`, `email`, `*_email` (suffix), `name`,
`full_name`. Behavior:
- secrets/tokens → replaced with `"[redacted]"`.
- `email` / `*_email` → drop the value and **emit `<key>_domain`** with the domain part
  (so `email="a@acme.com"` becomes `email_domain="acme.com"`).
- recurse into nested dicts/lists (covers `ActivityLog.detail` blobs).
- gated by `log_pii_redaction` (always on in prod; can disable in dev for debugging).

This is **defense-in-depth**: the convention is "log `email_domain`, never `email`"; the
processor enforces it even when a developer forgets. Fixes the existing leaks at
`authz_routes.py:259,267`.

### 7.5 Security/audit event helper — `service/src/logging_events.py` (new)

Thin helpers enforcing the envelope so call sites can't drift:

```python
def log_security(event, *, outcome, reason=None, **fields): ...   # category="security"
def log_audit(event, *, action=None, **fields): ...               # category="audit"
def log_access(...): ...                                          # used by AccessLog mw
```

Each injects `category` and validates `outcome`/`reason` shape. Call sites pass UUIDs and
domains, never raw PII (and the processor backstops them).

### 7.6 Rate-limit logging gap-fill

Emit `ratelimit.exceeded` (security, denied) in **both** `GlobalRateLimitMiddleware` (on
the 429 path) and `rate_limit_exceeded_handler` (slowapi), with `limit`, `http.route`,
`source_ip`, `actor` (if known). This is the single highest-value gap-fill for abuse and
anomaly detection.

### 7.7 Audit unification (`activity_service.log_activity`)

Keep the DB `ActivityLog` write unchanged (compliance queries depend on it). **Add** a
parallel structured emit at the end of `log_activity()`:
`log_audit("audit.activity", action=action, target_type=target_type,
target_id=str(target_id), actor=str(actor_id) if actor_id else "system",
workspace_id=str(workspace_id) if workspace_id else None, detail=detail)`.
The PII processor scrubs `detail`. Result: the SIEM/AI sees one unified stream while the
DB remains the queryable system-of-record.

### 7.8 Retrofit the 35 call sites

Normalize all existing `logger.*` calls to the envelope (event name from the vocabulary,
`category`, `outcome`, `reason`), route security/audit ones through the helpers, and
**remove raw email logging** (`authz_routes.py:259,267` → `email_domain`). Replace the
bare `logger.critical(e)` config-failure loop with `app.config.insecure` events carrying a
`reason`.

### 7.9 Admin SPA client logging + ingest (D5)

- **Client logger** (`admin/src/lib/logger.ts` or similar): structured `{event, level,
  fields}` shape; in dev → `console`; always captures `X-Request-ID` from API responses so
  a client error joins to its server line.
- **Signals shipped:** failed admin login, repeated 401/403, uncaught errors/rejections
  (via an error boundary + `window.onerror`/`onunhandledrejection`), batched and throttled.
- **Backend ingest endpoint:** `POST /internal/client-logs` — **admin-cookie auth**,
  rate-limited, strict payload schema (bounded size, capped batch, allowlisted event names),
  re-emits each as a `client.*` event (`category` security or app) into the same JSON
  stream with `source_ip` and `request_id` attached server-side. Client-supplied fields are
  treated as untrusted: validated, size-capped, and PII-redacted by the same processor.

## 8. Data flow

```
request
  → RequestContextMiddleware    bind request_id (+ actor/workspace/caller_service as resolved)
  → AccessLogMiddleware         start timer
  → … existing middleware …
  → route handler               log_security/log_audit events carry bound context automatically
  ← AccessLogMiddleware         emit ONE http.access event (status, duration_ms)
  ← RequestContextMiddleware    echo X-Request-ID; clear_contextvars
→ structlog processors (merge contextvars → PII redact → JSON)
→ stdout → swarm log driver → aggregator → AI anomaly pipeline
```

## 9. Error handling

- Unhandled exceptions → `app.error.unhandled` (error, `exc_info=True`, `error.type`,
  `request_id`). Existing `auth_routes.py:304` style is kept but normalized.
- Tracebacks never contain secrets (none are logged today; processor backstops).
- Logging must never raise into request handling: the PII processor and helpers are
  defensive (swallow their own errors, fall back to `[unserializable]`).

## 10. Anomaly-detection use cases this enables

Stable `event` + `outcome` + `reason` + correlation make these baseline-able:
- `auth.login.failed` / `authz.token.denied` velocity per `actor` and per `source_ip`
  (credential stuffing, brute force).
- `ratelimit.exceeded` spikes (abuse, scraping, DoS).
- `auth.token.reuse_detected` — near-zero baseline; any occurrence is a hard signal.
- New `(caller_service, workspace_id)` pairs (lateral movement / misconfig).
- 4xx/5xx ratio shifts per `http.route` (regressions, probing).
- `duration_ms` distribution drift per route (perf regressions, resource exhaustion).
- Cross-tenant access attempts via `authz.token.denied` reason `not_member`/`org_not_allowed`.

## 11. Testing (TDD)

Unit tests in `service/tests/` (matching existing style, e.g. `test_rate_limit_disable.py`):
- `test_logging_config`: JSON renderer output contains required envelope fields; level map.
- `test_pii_redaction`: emails → `*_domain`; tokens/secrets → `[redacted]`; nested dicts.
- `test_request_context`: mints ULID when absent; honors/validates inbound `X-Request-ID`;
  echoes header; binds + clears contextvars.
- `test_access_log`: exactly one `http.access` per request; route-template not raw path;
  status→level mapping; skip-list honored.
- `test_ratelimit_event`: 429 path emits `ratelimit.exceeded` (middleware + slowapi handler).
- `test_audit_dual_write`: `log_activity` writes DB row **and** emits `audit.activity`.
- `test_client_log_ingest`: `/internal/client-logs` rejects unauth/oversized/bad-schema;
  re-emits `client.*` with server-attached `source_ip`/`request_id`; redacts client PII.

## 12. Rollout / config defaults

- Prod defaults: `LOG_LEVEL=INFO`, `LOG_FORMAT=json`, `LOG_PII_REDACTION=true`.
- Dev `.env`: `LOG_FORMAT=console`, optionally `LOG_LEVEL=DEBUG`.
- No DB migration required (DB audit table unchanged).
- Document the schema + event vocabulary in `docs/` (operators + the AI pipeline contract).
- Update `.env.example` and deployment docs with the new flags.

## 13. Open risks / notes

- **contextvars + ASGI**: binding in middleware and clearing in `finally` is the standard
  structlog pattern; verified compatible with the async stack. One risk is background tasks
  spawned mid-request inheriting/leaking context — clear on exit mitigates.
- **Route template extraction**: Starlette exposes the matched route via
  `request.scope["route"]`; must handle the no-match (404) case → fall back to raw path
  (still redacted of obvious ID segments if feasible).
- **Client ingest abuse**: the `/internal/client-logs` endpoint is attack surface; strict
  auth + rate limit + bounded payload + allowlisted events are mandatory, not optional.
- **slowapi vs GlobalRateLimit duplication**: ensure we don't double-count a single 429 as
  two `ratelimit.exceeded` events; pick the authoritative emit point per code path.
