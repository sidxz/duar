# Structured Logging

Duar emits machine-parseable JSON log lines to stdout. Every line follows a
stable canonical envelope, making the stream suitable for log aggregators (Loki,
Elasticsearch, Datadog, Splunk, CloudWatch) and AI anomaly-detection pipelines
without per-deployment schema translation.

---

## Configuration

Three environment variables control logging behaviour:

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Minimum log level: `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` \| `CRITICAL` |
| `LOG_FORMAT` | `json` | Renderer: `json` for production (one JSON object per line); `console` for development (human-readable colour output) |
| `LOG_PII_REDACTION` | `true` | When `true`, emails and secrets are masked before the line is written. Always `true` in production. |

**Recommended settings:**

```bash
# Production
LOG_LEVEL=INFO
LOG_FORMAT=json
LOG_PII_REDACTION=true

# Development
LOG_LEVEL=DEBUG
LOG_FORMAT=console
LOG_PII_REDACTION=false   # optional — disable only for local debugging
```

---

## Canonical Log Envelope

Every log line carries this base schema. Event-specific fields are added on top.

| Field | Type | Always present | Description |
|-------|------|----------------|-------------|
| `ts` | string (ISO 8601 UTC) | yes | Timestamp: `2026-06-16T12:34:56.789Z` |
| `level` | string | yes | `debug` \| `info` \| `warning` \| `error` \| `critical` |
| `event` | string | yes | Stable dotted event name from the [vocabulary](#event-vocabulary) |
| `category` | string | yes | `access` \| `security` \| `audit` \| `app` |
| `outcome` | string | no | `success` \| `failure` \| `denied` \| `error` (omitted for pure informational lines) |
| `reason` | string | no | Machine-readable reason code (`snake_case`, finite vocabulary) |
| `service` | string | yes | Constant: `duar` |
| `version` | string | yes | Service version, e.g. `0.12.0` |
| `env` | string | yes | `prod` when `DEBUG=false`; `dev` otherwise |
| `request_id` | string | no | ULID; correlates all lines produced within one HTTP request |
| `actor` | string | no | User UUID or `"anonymous"` |
| `workspace_id` | string | no | Workspace UUID, when known |
| `caller_service` | string | no | `service_name` of the calling service key, when known |
| `source_ip` | string | no | Client IP (rightmost hop, respects `TRUSTED_PROXY_COUNT`) |

### Sample JSON line

```json
{
  "ts": "2026-06-16T12:34:56.789Z",
  "level": "warning",
  "event": "auth.login.failed",
  "category": "security",
  "outcome": "failure",
  "reason": "invalid_credentials",
  "service": "duar",
  "version": "0.12.0",
  "env": "prod",
  "request_id": "01J9XKQM5F3V8P2NR0HT6WB7YC",
  "actor": "anonymous",
  "source_ip": "203.0.113.4",
  "provider": "google",
  "email_domain": "acme.com"
}
```

---

## Event Vocabulary

All event names are a **closed, documented vocabulary** in the form `domain.object.action`.
New events must be added to this table; ad-hoc names are not permitted — this stability is
what makes the stream learnable by an anomaly-detection pipeline.

| Event | Category | Typical outcome | Key fields |
|-------|----------|-----------------|------------|
| `http.access` | access | success / failure / error | `http.method`, `http.route`, `http.status`, `duration_ms`, `resp_bytes` |
| `auth.login.succeeded` | security | success | `provider`, `actor` |
| `auth.login.failed` | security | failure | `provider`, `reason` |
| `auth.token.issued` | security | success | `token_type`, `actor` |
| `auth.token.refreshed` | security | success | `actor` |
| `auth.token.reuse_detected` | security | failure | `actor`, `jti` (hashed) — hard compromise signal |
| `auth.token.revoked` | security | success | `actor`, `count` |
| `authz.token.issued` | security | success | `caller_service`, `workspace_role`, `actions_count` |
| `authz.token.denied` | security | denied | `reason`, `caller_service` |
| `authz.idp.validation_failed` | security | failure | `provider`, `reason` |
| `ratelimit.exceeded` | security | denied | `limit`, `http.route`, `source_ip` |
| `permission.registered` | audit | success | `caller_service`, `resource_type`, `resource_id` |
| `permission.share.granted` | audit | success | `resource_type`, `resource_id`, `grantee` |
| `permission.share.revoked` | audit | success | `resource_type`, `resource_id`, `grantee` |
| `permission.visibility.updated` | audit | success | `resource_type`, `resource_id` |
| `permission.deregistered` | audit | success | `resource_type`, `resource_id` |
| `admin.action` | audit | success | `action`, `target_type`, `target_id` |
| `audit.activity` | audit | success | `action`, `target_type`, `target_id`, `actor`, `workspace_id` |
| `app.startup` | app | — | `port` |
| `app.shutdown` | app | — | — |
| `app.config.insecure` | app | failure | `reason` |
| `app.error.unhandled` | app | error | `error.type`, `error` (+ stack trace) |
| `client.*` | security / app | — | Forwarded admin-SPA events (prefixed `client.`) |

### `authz.token.denied` reason codes

| Reason | Meaning |
|--------|---------|
| `not_member` | User is not a member of the requested workspace |
| `inactive_user` | User account is deactivated |
| `email_conflict` | Email does not match the workspace's allowed domain |
| `org_not_allowed` | Organisation restriction blocks the request |
| `org_not_permitted` | Organisation-level permission denied |

---

## PII Policy

**Never log raw email addresses.** The service follows a strict redaction policy:

- **Emails** — the raw value is dropped and replaced with `<key>_domain` containing
  only the domain part. For example, `email="alice@acme.com"` becomes
  `email_domain="acme.com"`. This preserves tenancy signals (domain-level anomalies
  are detectable) without exposing personal data.
- **Secrets and tokens** — `password`, `token`, `access_token`, `refresh_token`,
  `id_token`, `authorization`, `cookie`, `service_key`, `api_key`, `client_secret`,
  `secret`, `jwt` are replaced with `"[redacted]"`.
- **Names** — `name` and `full_name` are replaced with `"[redacted]"`.

The PII redaction processor is applied to every log line, including nested objects
(e.g. `ActivityLog.detail` blobs). Setting `LOG_PII_REDACTION=false` bypasses it
— intended only for local debugging, never in production.

**The convention:** log `email_domain`, never `email`. The processor enforces this
even when a call site forgets.

---

## Anomaly Detection Use Cases

The stable `event + outcome + reason + request_id` envelope makes the following
patterns baseline-able and alertable without custom parsing:

| Signal | Query shape | What it catches |
|--------|-------------|-----------------|
| Credential stuffing / brute force | `auth.login.failed` velocity per `source_ip` or `actor` | Repeated login failures from one IP or against one account |
| Token compromise | `auth.token.reuse_detected` any occurrence | Near-zero baseline; any hit is a hard compromise signal |
| Rate-limit abuse / DoS | `ratelimit.exceeded` spikes per `source_ip` or `http.route` | Scraping, enumeration, DDoS probes |
| Lateral movement / misconfiguration | New `(caller_service, workspace_id)` pairs in `authz.token.issued` | A service accessing workspaces it has never touched before |
| Cross-tenant access attempts | `authz.token.denied` with `reason=not_member` or `reason=org_not_allowed` | Users probing workspaces they don't belong to |
| Route-level error regressions | 4xx/5xx ratio shifts per `http.route` in `http.access` | Broken deploys, probing, schema mismatches |
| Performance regressions | `duration_ms` distribution drift per `http.route` | Resource exhaustion, slow queries, dependency degradation |

---

## Log Categories

| Category | Volume | Routing suggestion |
|----------|--------|--------------------|
| `access` | One line per request | General access logs; source for latency dashboards |
| `security` | Auth, authz, rate-limit events | SIEM / alert stream |
| `audit` | State-changing admin/business actions | Compliance log; mirrors DB `ActivityLog` table |
| `app` | Startup, background tasks, unhandled errors | Ops / on-call stream |

!!! note "DB audit table unchanged"
    The `ActivityLog` database table is the queryable system-of-record for compliance.
    The structured log stream is additive — both are written for every auditable action.
    No database migration is required to enable structured logging.

---

## Access Log Fields

The `http.access` event (one per request, emitted by `AccessLogMiddleware`) carries
these additional fields on top of the envelope:

| Field | Description |
|-------|-------------|
| `http.method` | HTTP verb: `GET`, `POST`, etc. |
| `http.route` | Route template (`/users/{user_id}`), not the raw URL — prevents PII-in-URL and high cardinality |
| `http.status` | HTTP response status code |
| `duration_ms` | End-to-end request latency in milliseconds |
| `resp_bytes` | Response body size in bytes |
| `user_agent` | `User-Agent` header value |

Level is derived from status: `< 400` → `info`, `4xx` → `warning`, `5xx` → `error`.

Health-check and OpenAPI paths (`/health`, `/docs`, `/redoc`, `/openapi.json`,
`/.well-known/jwks.json`) are excluded from access logging to reduce noise.
