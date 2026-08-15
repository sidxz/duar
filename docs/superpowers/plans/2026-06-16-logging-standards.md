# Logging Standards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Standardize logging across the Duar service (and admin SPA) into a single structured JSON pipeline with request correlation, access logging, a security/audit event taxonomy, and PII redaction — sufficient to feed an AI anomaly-detection pipeline.

**Architecture:** Configure the already-present `structlog` once and route stdlib logging through it via `ProcessorFormatter`, so app + framework logs share one JSON schema. Two pure-ASGI middlewares (outermost) provide a per-request `request_id` (via `contextvars`) and a single `http.access` event. A PII redaction processor enforces "never log raw email/secrets" at render time. Thin event helpers (`log_security`/`log_audit`/`log_access`) enforce a stable envelope. Gap-fills add the currently-silent rate-limit events and dual-emit DB audit rows as log events. The admin SPA ships security-relevant client events to a new authenticated backend ingest endpoint that re-emits them into the same stream.

**Tech Stack:** Python 3.12, FastAPI/Starlette, structlog ≥24.4 (already a dep), pydantic-settings, slowapi; admin: Vite + React 19 + TypeScript.

**Spec:** `docs/superpowers/specs/2026-06-16-logging-standards-design.md`

## Global Constraints

- **No new backend runtime dependencies.** structlog is already present; `request_id` uses `uuid.uuid4().hex` (no ULID lib).
- **JSON to stdout** is the prod transport (`LOG_FORMAT=json`); dev uses `console`.
- **PII rule:** never log raw `email`/`name`/secrets. The redaction processor enforces it; call sites log `email_domain`, never `email`.
- **Pure ASGI** (not `BaseHTTPMiddleware`) for `RequestContext`/`AccessLog` — `BaseHTTPMiddleware` does not propagate contextvars to the endpoint. Both are added **after** `DynamicCORSMiddleware` so they sit outermost (`add_middleware` puts the last-added on the outside).
- **Event names come from the documented vocabulary** (spec §6); envelope fields are stable: `ts, level, event, category, outcome, reason, service, version, env, request_id, actor, workspace_id, caller_service, source_ip`.
- **structlog config uses `cache_logger_on_first_use=False`** so `structlog.testing.capture_logs` works reliably in tests (negligible runtime cost).
- **Tests:** run `cd service && uv run pytest`. Sync `TestClient` from `fastapi.testclient`; `monkeypatch.setattr(settings, …)`; imports as `from src…`. No `conftest.py` exists — keep tests self-contained.
- **Lint:** run `make fmt` (ruff) before every commit.

## File Structure

**Create (backend):**
- `service/src/logging_redaction.py` — PII/secret redaction core + structlog processor (Task 2)
- `service/src/logging_config.py` — `configure_logging()` central setup (Task 3)
- `service/src/logging_events.py` — `log_security`/`log_audit`/`log_access` helpers (Task 4)
- `service/src/middleware/request_context.py` — `RequestContextMiddleware` + `bind_identity()` (Task 5)
- `service/src/middleware/access_log.py` — `AccessLogMiddleware` (Task 6)
- `service/src/api/client_log_routes.py` — `/internal/client-logs` ingest endpoint (Task 10)

**Modify (backend):**
- `service/src/config.py` — logging flags + `environment` property (Task 1)
- `service/src/main.py` — call `configure_logging()`; register the two new middlewares (Tasks 3, 6)
- `service/src/middleware/rate_limit.py` — emit `ratelimit.exceeded` (Task 7)
- `service/src/services/activity_service.py` — dual-emit `audit.activity` (Task 8)
- `service/src/api/authz_routes.py`, `auth_routes.py`, `admin_routes.py`, `permission_routes.py`, `middleware/cors.py` — retrofit to envelope + fix PII leaks (Task 9)

**Create/Modify (admin SPA):**
- `admin/src/lib/logger.ts` — client logger (buffer/flush) (Task 11)
- `admin/src/components/ErrorBoundary.tsx` — error boundary (Task 11)
- `admin/src/api/client.ts`, `admin/src/main.tsx`, `admin/src/pages/Login.tsx` — wire client logging (Task 11)

**Docs:**
- `docs/observability/logging.md` + nav + `.env` example + deployment docs (Task 12)

**Tests (create):** `service/tests/test_logging_config.py`, `test_logging_redaction.py`, `test_logging_events.py`, `test_request_context_mw.py`, `test_access_log_mw.py`, `test_ratelimit_event.py`, `test_audit_dual_emit.py`, `test_no_raw_pii_logging.py`, `test_client_log_ingest.py`.

---

## Phase 1 — Foundation

### Task 1: Logging config flags

**Files:**
- Modify: `service/src/config.py` (add fields after the Admin block ~line 78; add `environment` property near other `@property`)
- Test: `service/tests/test_logging_config.py`

**Interfaces:**
- Produces: `settings.log_level: str`, `settings.log_format: str`, `settings.log_pii_redaction: bool`, `settings.environment -> str` ("dev"/"prod")

- [ ] **Step 1: Write the failing test**

Create `service/tests/test_logging_config.py`:

```python
from src.config import Settings, settings


def test_logging_defaults():
    s = Settings()
    assert s.log_level == "INFO"
    assert s.log_format == "json"
    assert s.log_pii_redaction is True


def test_environment_property(monkeypatch):
    monkeypatch.setattr(settings, "debug", True)
    assert settings.environment == "dev"
    monkeypatch.setattr(settings, "debug", False)
    assert settings.environment == "prod"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_logging_config.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'log_level'`

- [ ] **Step 3: Add the config fields and property**

In `service/src/config.py`, add inside `Settings` (after the `# Admin` block):

```python
    # Logging
    log_level: str = "INFO"  # DEBUG|INFO|WARNING|ERROR|CRITICAL
    log_format: str = "json"  # "json" (prod) | "console" (dev)
    log_pii_redaction: bool = True  # mask emails/secrets in logs (always on in prod)
```

And add this property alongside the other `@property` definitions:

```python
    @property
    def environment(self) -> str:
        """Coarse env label for log lines; derived from DEBUG."""
        return "dev" if self.debug else "prod"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd service && uv run pytest tests/test_logging_config.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
make fmt
git add service/src/config.py service/tests/test_logging_config.py
git commit -m "feat(logging): add LOG_LEVEL/LOG_FORMAT/LOG_PII_REDACTION config + environment"
```

---

### Task 2: PII / secret redaction processor

**Files:**
- Create: `service/src/logging_redaction.py`
- Test: `service/tests/test_logging_redaction.py`

**Interfaces:**
- Produces:
  - `redact_mapping(data: dict) -> dict` — pure, recursive; reusable for untrusted input (Task 10)
  - `redact_processor(logger, method_name, event_dict: dict) -> dict` — structlog processor signature (Task 3)

- [ ] **Step 1: Write the failing test**

Create `service/tests/test_logging_redaction.py`:

```python
from src.logging_redaction import redact_mapping, redact_processor


def _r(d):
    return redact_processor(None, "info", dict(d))


def test_email_becomes_domain():
    out = _r({"email": "alice@acme.com"})
    assert "email" not in out
    assert out["email_domain"] == "acme.com"


def test_suffixed_email_key_becomes_domain():
    out = _r({"actor_email": "bob@corp.io"})
    assert "actor_email" not in out
    assert out["actor_email_domain"] == "corp.io"


def test_secrets_redacted():
    out = _r({"access_token": "x", "service_key": "k", "password": "p", "name": "Bob"})
    assert out["access_token"] == "[redacted]"
    assert out["service_key"] == "[redacted]"
    assert out["password"] == "[redacted]"
    assert out["name"] == "[redacted]"


def test_non_sensitive_keys_preserved():
    out = _r({"service_name": "docu", "workspace_id": "ws1", "actor": "u1"})
    assert out == {"service_name": "docu", "workspace_id": "ws1", "actor": "u1"}


def test_nested_and_list_redaction():
    out = _r({"detail": {"email": "x@y.com", "token": "t"}, "items": [{"jwt": "j"}]})
    assert out["detail"]["email_domain"] == "y.com"
    assert out["detail"]["token"] == "[redacted]"
    assert out["items"][0]["jwt"] == "[redacted]"


def test_redact_mapping_is_pure():
    src = {"email": "a@b.com"}
    redact_mapping(src)
    assert src == {"email": "a@b.com"}  # input untouched
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_logging_redaction.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.logging_redaction'`

- [ ] **Step 3: Write the implementation**

Create `service/src/logging_redaction.py`:

```python
"""Defense-in-depth redaction of PII and secrets from log event dicts.

The convention is "log email_domain, never email"; this processor enforces it
even when a developer forgets, and scrubs a denylist of secret-bearing keys.
"""

_REDACTED = "[redacted]"

# Exact (case-insensitive) key matches. Email is handled separately (see _is_email).
_DENY_EXACT = frozenset(
    {
        "password",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "authorization",
        "cookie",
        "set-cookie",
        "service_key",
        "api_key",
        "client_secret",
        "secret",
        "jwt",
        "name",
        "full_name",
    }
)


def _is_email_key(key: str) -> bool:
    return key == "email" or key.endswith("_email")


def _domain(value: object) -> str | None:
    if isinstance(value, str) and "@" in value:
        return value.rsplit("@", 1)[-1]
    return None


def redact_mapping(data: dict) -> dict:
    """Return a redacted shallow-rebuilt copy of ``data`` (recurses into dict/list)."""
    out: dict = {}
    for key, value in data.items():
        kl = key.lower() if isinstance(key, str) else key
        if isinstance(kl, str) and _is_email_key(kl):
            dom = _domain(value)
            if dom is not None:
                out[f"{key}_domain"] = dom
            continue  # drop the raw email value entirely
        if isinstance(kl, str) and kl in _DENY_EXACT:
            out[key] = _REDACTED
            continue
        out[key] = _redact_value(value)
    return out


def _redact_value(value: object) -> object:
    if isinstance(value, dict):
        return redact_mapping(value)
    if isinstance(value, (list, tuple)):
        return type(value)(_redact_value(v) for v in value)
    return value


def redact_processor(logger, method_name, event_dict: dict) -> dict:
    """structlog processor: redact PII/secrets. Never raises into the log path."""
    try:
        return redact_mapping(event_dict)
    except Exception:
        return event_dict
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd service && uv run pytest tests/test_logging_redaction.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
make fmt
git add service/src/logging_redaction.py service/tests/test_logging_redaction.py
git commit -m "feat(logging): PII/secret redaction processor (email->domain, secret denylist)"
```

---

### Task 3: Central logging configuration

**Files:**
- Create: `service/src/logging_config.py`
- Modify: `service/src/main.py` (call `configure_logging()` first thing in `lifespan`, line ~54)
- Test: `service/tests/test_logging_config.py` (extend)

**Interfaces:**
- Consumes: `settings.*` (Task 1), `redact_processor` (Task 2), `src.version.__version__`
- Produces: `configure_logging() -> None` — idempotent; configures structlog + stdlib root → JSON/console to stdout

- [ ] **Step 1: Write the failing test (extend Task 1's file)**

Append to `service/tests/test_logging_config.py`:

```python
import json

import structlog

from src.logging_config import configure_logging


def test_configure_emits_json_envelope(capsys, monkeypatch):
    monkeypatch.setattr(settings, "log_format", "json")
    monkeypatch.setattr(settings, "log_level", "INFO")
    configure_logging()
    structlog.get_logger().info("test.event", foo="bar")
    line = capsys.readouterr().out.strip().splitlines()[-1]
    rec = json.loads(line)
    assert rec["event"] == "test.event"
    assert rec["level"] == "info"
    assert rec["service"] == "duar"
    assert rec["version"]
    assert rec["ts"]
    assert rec["foo"] == "bar"


def test_configure_redacts_email_end_to_end(capsys, monkeypatch):
    monkeypatch.setattr(settings, "log_format", "json")
    monkeypatch.setattr(settings, "log_pii_redaction", True)
    configure_logging()
    structlog.get_logger().warning("authz.token.denied", email="a@acme.com")
    rec = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "email" not in rec
    assert rec["email_domain"] == "acme.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_logging_config.py -k configure -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.logging_config'`

- [ ] **Step 3: Write the implementation**

Create `service/src/logging_config.py`:

```python
"""Central structlog configuration. Call configure_logging() once at startup.

Routes stdlib logging (uvicorn/sqlalchemy/authlib/slowapi) through structlog so
app and framework logs share one JSON schema.
"""

import logging
import sys

import structlog

from src.config import settings
from src.logging_redaction import redact_processor
from src.version import __version__


def _add_service_context(logger, method_name, event_dict: dict) -> dict:
    event_dict.setdefault("service", "duar")
    event_dict.setdefault("version", __version__)
    event_dict.setdefault("env", settings.environment)
    return event_dict


def configure_logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
        _add_service_context,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if settings.log_pii_redaction:
        shared_processors.append(redact_processor)

    structlog.configure(
        processors=shared_processors
        + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    if settings.log_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # We own access logs (AccessLogMiddleware); silence uvicorn's and tame SQL noise.
    logging.getLogger("uvicorn.access").handlers.clear()
    logging.getLogger("uvicorn.access").propagate = False
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
```

- [ ] **Step 4: Wire into startup**

In `service/src/main.py`, make `configure_logging()` the first line of `lifespan` (before the existing `logger.info("daikon-duar starting", ...)` at line 54). Add the import near the top:

```python
from src.logging_config import configure_logging
```

Then in `lifespan`:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info("app.startup", port=settings.service_port)
    await _run_migrations()
    logger.info("app.db.migrated")
    ...
```

(Renaming the two startup events to the vocabulary is part of this step; the rest of `main.py`'s call sites are normalized in Task 9.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd service && uv run pytest tests/test_logging_config.py -v`
Expected: PASS (all 4 tests). If `cache_logger_on_first_use` flakiness appears across the full suite, confirm it is `False`.

- [ ] **Step 6: Commit**

```bash
make fmt
git add service/src/logging_config.py service/src/main.py service/tests/test_logging_config.py
git commit -m "feat(logging): central structlog JSON config + stdlib bridge, wired at startup"
```

---

### Task 4: Event helpers (security/audit/access)

**Files:**
- Create: `service/src/logging_events.py`
- Test: `service/tests/test_logging_events.py`

**Interfaces:**
- Produces:
  - `log_security(event: str, *, outcome: str, reason: str | None = None, level: str | None = None, **fields) -> None`
  - `log_audit(event: str = "audit.activity", *, action: str | None = None, **fields) -> None`
  - `log_access(event: str = "http.access", *, level: str = "info", **fields) -> None`
  - `VALID_OUTCOMES: frozenset[str]`

- [ ] **Step 1: Write the failing test**

Create `service/tests/test_logging_events.py`:

```python
import pytest
from structlog.testing import capture_logs

from src.logging_events import log_audit, log_security


def test_log_security_shape_and_level():
    with capture_logs() as logs:
        log_security("authz.token.denied", outcome="denied", reason="not_member", actor="u1")
    e = logs[0]
    assert e["event"] == "authz.token.denied"
    assert e["category"] == "security"
    assert e["outcome"] == "denied"
    assert e["reason"] == "not_member"
    assert e["log_level"] == "warning"  # denied/failure -> warning


def test_log_security_success_is_info():
    with capture_logs() as logs:
        log_security("auth.login.succeeded", outcome="success", actor="u1")
    assert logs[0]["log_level"] == "info"


def test_log_security_rejects_bad_outcome():
    with pytest.raises(ValueError):
        log_security("x.y", outcome="bogus")


def test_log_audit_shape():
    with capture_logs() as logs:
        log_audit(action="workspace_created", target_type="workspace", actor="u1")
    e = logs[0]
    assert e["event"] == "audit.activity"
    assert e["category"] == "audit"
    assert e["action"] == "workspace_created"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_logging_events.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.logging_events'`

- [ ] **Step 3: Write the implementation**

Create `service/src/logging_events.py`:

```python
"""Thin helpers that enforce the log envelope so call sites can't drift.

Loggers are fetched per call (no module-level cache) so structlog.testing
.capture_logs works reliably in tests.
"""

import structlog

VALID_OUTCOMES = frozenset({"success", "failure", "denied", "error"})


def log_security(event, *, outcome, reason=None, level=None, **fields) -> None:
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"invalid outcome: {outcome!r}")
    payload = {"category": "security", "outcome": outcome, **fields}
    if reason is not None:
        payload["reason"] = reason
    method = level or ("warning" if outcome in {"failure", "denied"} else "info")
    getattr(structlog.get_logger(), method)(event, **payload)


def log_audit(event="audit.activity", *, action=None, **fields) -> None:
    payload = {"category": "audit", **fields}
    if action is not None:
        payload["action"] = action
    structlog.get_logger().info(event, **payload)


def log_access(event="http.access", *, level="info", **fields) -> None:
    getattr(structlog.get_logger(), level)(event, category="access", **fields)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd service && uv run pytest tests/test_logging_events.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
make fmt
git add service/src/logging_events.py service/tests/test_logging_events.py
git commit -m "feat(logging): log_security/log_audit/log_access envelope helpers"
```

---

## Phase 2 — Correlation & Access

### Task 5: Request correlation middleware

**Files:**
- Create: `service/src/middleware/request_context.py`
- Test: `service/tests/test_request_context_mw.py`

**Interfaces:**
- Produces:
  - `class RequestContextMiddleware` (pure ASGI: `__init__(self, app)`)
  - `bind_identity(request, **fields) -> None` — binds non-None fields to contextvars AND `request.scope["state"]`
  - `REQUEST_ID_HEADER = "x-request-id"`

- [ ] **Step 1: Write the failing test**

Create `service/tests/test_request_context_mw.py`:

```python
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from src.middleware.request_context import RequestContextMiddleware, bind_identity


def _app():
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/ctx")
    def ctx():
        return dict(structlog.contextvars.get_contextvars())

    return app


def test_mints_request_id_and_echoes_header():
    r = TestClient(_app()).get("/ctx")
    assert "x-request-id" in {k.lower() for k in r.headers}
    rid = r.headers["x-request-id"]
    assert r.json()["request_id"] == rid  # contextvar visible in handler


def test_honors_valid_inbound_request_id():
    r = TestClient(_app()).get("/ctx", headers={"X-Request-ID": "abc123def456"})
    assert r.headers["x-request-id"] == "abc123def456"
    assert r.json()["request_id"] == "abc123def456"


def test_rejects_invalid_inbound_request_id():
    r = TestClient(_app()).get("/ctx", headers={"X-Request-ID": "bad id !!"})
    assert r.headers["x-request-id"] != "bad id !!"


def test_bind_identity_sets_contextvars_and_state():
    scope = {"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}
    req = Request(scope)
    structlog.contextvars.clear_contextvars()
    bind_identity(req, actor="u1", workspace_id="ws1", caller_service=None)
    assert structlog.contextvars.get_contextvars().get("actor") == "u1"
    assert req.scope["state"]["actor"] == "u1"
    assert "caller_service" not in req.scope["state"]  # None dropped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_request_context_mw.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.middleware.request_context'`

- [ ] **Step 3: Write the implementation**

Create `service/src/middleware/request_context.py`:

```python
"""Per-request correlation. Pure ASGI (NOT BaseHTTPMiddleware) so contextvars
bound here propagate into the route handler.
"""

import uuid

import structlog

REQUEST_ID_HEADER = "x-request-id"
_MAX_ID_LEN = 64


def _valid_request_id(value: str) -> bool:
    return bool(value) and len(value) <= _MAX_ID_LEN and value.replace("-", "").isalnum()


def bind_identity(request, **fields) -> None:
    """Bind resolved identity (actor/workspace_id/caller_service/...) to the log
    context and request state. None values are dropped."""
    clean = {k: v for k, v in fields.items() if v is not None}
    if not clean:
        return
    structlog.contextvars.bind_contextvars(**clean)
    request.scope.setdefault("state", {}).update(clean)


class RequestContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        raw = headers.get(REQUEST_ID_HEADER.encode())
        candidate = raw.decode("latin-1") if raw else ""
        request_id = candidate if _valid_request_id(candidate) else uuid.uuid4().hex

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"].append(
                    (REQUEST_ID_HEADER.encode(), request_id.encode())
                )
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            structlog.contextvars.clear_contextvars()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd service && uv run pytest tests/test_request_context_mw.py -v`
Expected: PASS (4 tests). The `request_id` appearing in the handler's JSON body confirms contextvar propagation (would fail with BaseHTTPMiddleware).

- [ ] **Step 5: Commit**

```bash
make fmt
git add service/src/middleware/request_context.py service/tests/test_request_context_mw.py
git commit -m "feat(logging): request_id correlation middleware + bind_identity helper"
```

---

### Task 6: Access log middleware

**Files:**
- Create: `service/src/middleware/access_log.py`
- Modify: `service/src/main.py` (register both new middlewares after `DynamicCORSMiddleware`, ~line 183)
- Test: `service/tests/test_access_log_mw.py`

**Interfaces:**
- Consumes: `log_access` (Task 4), `get_client_ip` (rate_limit.py)
- Produces: `class AccessLogMiddleware` (pure ASGI; `__init__(self, app, skip_paths=None)`)

- [ ] **Step 1: Write the failing test**

Create `service/tests/test_access_log_mw.py`:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from src.middleware.access_log import AccessLogMiddleware


def _app():
    app = FastAPI()
    app.add_middleware(AccessLogMiddleware)

    @app.get("/echo/{name}")
    def echo(name: str):
        return {"name": name}

    return app


def test_emits_single_access_event_with_route_template():
    with capture_logs() as logs:
        TestClient(_app()).get("/echo/alice")
    access = [e for e in logs if e["event"] == "http.access"]
    assert len(access) == 1
    e = access[0]
    assert e["category"] == "access"
    assert e["http.route"] == "/echo/{name}"  # template, not /echo/alice
    assert e["http.method"] == "GET"
    assert e["http.status"] == 200
    assert "duration_ms" in e
    assert e["log_level"] == "info"


def test_4xx_logged_as_warning():
    with capture_logs() as logs:
        TestClient(_app()).get("/nope")
    e = [x for x in logs if x["event"] == "http.access"][0]
    assert e["http.status"] == 404
    assert e["log_level"] == "warning"


def test_skips_health():
    app = FastAPI()
    app.add_middleware(AccessLogMiddleware)

    @app.get("/health")
    def health():
        return {"ok": True}

    with capture_logs() as logs:
        TestClient(app).get("/health")
    assert not [e for e in logs if e.get("event") == "http.access"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_access_log_mw.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.middleware.access_log'`

- [ ] **Step 3: Write the implementation**

Create `service/src/middleware/access_log.py`:

```python
"""Single structured access-log event per request. Pure ASGI, sits outermost."""

import time

from starlette.requests import Request
from starlette.routing import Match

from src.logging_events import log_access
from src.middleware.rate_limit import get_client_ip

_SKIP_PATHS = {
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/.well-known/jwks.json",
}


def _route_template(scope) -> str:
    """Low-cardinality route template (e.g. /echo/{name}) via re-match against
    the app's routes; falls back to the raw path when nothing matches (404)."""
    path = scope.get("path", "")
    app = scope.get("app")
    if app is None:
        return path
    for route in getattr(app, "routes", []):
        try:
            match, _ = route.matches(scope)
        except Exception:
            continue
        if match == Match.FULL:
            return getattr(route, "path", path)
    return path


class AccessLogMiddleware:
    def __init__(self, app, skip_paths=None):
        self.app = app
        self.skip_paths = skip_paths or _SKIP_PATHS

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") in self.skip_paths:
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        state = {"status": 500, "bytes": 0}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
            elif message["type"] == "http.response.body":
                state["bytes"] += len(message.get("body", b"") or b"")
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            try:
                status = state["status"]
                level = "info" if status < 400 else "warning" if status < 500 else "error"
                shared = scope.get("state") or {}
                fields = {
                    "http.method": scope.get("method"),
                    "http.route": _route_template(scope),
                    "http.status": status,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                    "resp_bytes": state["bytes"],
                    "source_ip": get_client_ip(Request(scope)),
                }
                for k in ("actor", "workspace_id", "caller_service"):
                    if shared.get(k) is not None:
                        fields[k] = shared[k]
                log_access("http.access", level=level, **fields)
            except Exception:
                pass  # never let logging break the response
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd service && uv run pytest tests/test_access_log_mw.py -v`
Expected: PASS (3 tests). If `http.route` returns the raw path instead of the template, verify `route.matches(scope)` against the installed Starlette version and adjust `_route_template`.

- [ ] **Step 5: Register both middlewares in `main.py`**

In `service/src/main.py`, immediately after `app.add_middleware(DynamicCORSMiddleware)` (line 183), add:

```python
from src.middleware.access_log import AccessLogMiddleware
from src.middleware.request_context import RequestContextMiddleware

app.add_middleware(AccessLogMiddleware)        # inside RequestContext
app.add_middleware(RequestContextMiddleware)   # last added = outermost
```

(Move the imports to the top with the other middleware imports.) Result order
(outermost→innermost): `RequestContext → AccessLog → DynamicCORS → TrustedHost → Session → SecurityHeaders → GlobalRateLimit → MaxBodySize → app`.

- [ ] **Step 6: Verify the app boots and emits access logs**

Run: `cd service && uv run python -c "from src.main import app; from fastapi.testclient import TestClient; print(TestClient(app).get('/health').status_code)"`
Expected: prints `200` with no import/middleware errors. (Health is skip-listed, so no access line — that's correct.)

- [ ] **Step 7: Commit**

```bash
make fmt
git add service/src/middleware/access_log.py service/src/main.py service/tests/test_access_log_mw.py
git commit -m "feat(logging): access-log middleware (single http.access event) + wire outermost"
```

---

## Phase 3 — Coverage gap-fills

### Task 7: Rate-limit exceeded events

**Files:**
- Modify: `service/src/middleware/rate_limit.py` (the two 429 return paths in `GlobalRateLimitMiddleware.dispatch`; the slowapi `rate_limit_exceeded_handler`)
- Test: `service/tests/test_ratelimit_event.py`

**Interfaces:**
- Consumes: `log_security` (Task 4), existing `get_client_ip`

- [ ] **Step 1: Write the failing test**

Create `service/tests/test_ratelimit_event.py`:

```python
import pytest
from starlette.requests import Request
from structlog.testing import capture_logs

import src.middleware.rate_limit as rl
from src.config import settings


def _request(path="/x", ip="10.0.0.9"):
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
async def test_global_limit_emits_event_when_exceeded(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)

    class _R:
        async def eval(self, *a):
            return 999  # over the limit

        async def ttl(self, *a):
            return 30

    async def _get():
        return _R()

    monkeypatch.setattr(rl, "_get_redis", _get)
    mw = rl.GlobalRateLimitMiddleware(app=None, requests_per_minute=1)

    async def call_next(_req):
        raise AssertionError("must not pass through when over the limit")

    with capture_logs() as logs:
        resp = await mw.dispatch(_request(), call_next)

    assert resp.status_code == 429
    events = [e for e in logs if e["event"] == "ratelimit.exceeded"]
    assert events
    assert events[0]["outcome"] == "denied"
    assert events[0]["source_ip"] == "10.0.0.9"
    assert events[0]["http.route"] == "/x"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_ratelimit_event.py -v`
Expected: FAIL — no `ratelimit.exceeded` event found.

- [ ] **Step 3: Add the events**

In `service/src/middleware/rate_limit.py`, add the import at the top:

```python
from src.logging_events import log_security
```

In `GlobalRateLimitMiddleware.dispatch`, before the Redis-path 429 return (currently line ~112):

```python
            if count > self.rpm:
                ttl = await r.ttl(key)
                log_security(
                    "ratelimit.exceeded",
                    outcome="denied",
                    reason="global_ip",
                    limit=self.rpm,
                    source_ip=ip,
                    **{"http.route": request.url.path},
                )
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests"},
                    headers={"Retry-After": str(max(ttl, 1))},
                )
```

And in the in-memory fallback branch, before the fallback 429 return (currently line ~126):

```python
            if len(_fallback_counts[key]) >= _FALLBACK_LIMIT:
                log_security(
                    "ratelimit.exceeded",
                    outcome="denied",
                    reason="global_ip_fallback",
                    limit=_FALLBACK_LIMIT,
                    source_ip=ip,
                    **{"http.route": request.url.path},
                )
                return Response(status_code=429, content="Rate limit exceeded")
```

In `rate_limit_exceeded_handler` (slowapi per-route limits), before the return:

```python
async def rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    log_security(
        "ratelimit.exceeded",
        outcome="denied",
        reason="route_limit",
        source_ip=get_client_ip(request),
        **{"http.route": request.url.path},
    )
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests"},
        headers={"Retry-After": str(exc.retry_after)},
    )
```

(The `reason` codes — `global_ip` / `global_ip_fallback` / `route_limit` — distinguish the three paths so a single 429 is never double-counted.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd service && uv run pytest tests/test_ratelimit_event.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
make fmt
git add service/src/middleware/rate_limit.py service/tests/test_ratelimit_event.py
git commit -m "feat(logging): emit ratelimit.exceeded on all 429 paths (was silent)"
```

---

### Task 8: Audit unification (dual-emit)

**Files:**
- Modify: `service/src/services/activity_service.py` (`log_activity`, lines 11-30)
- Test: `service/tests/test_audit_dual_emit.py`

**Interfaces:**
- Consumes: `log_audit` (Task 4)

- [ ] **Step 1: Write the failing test**

Create `service/tests/test_audit_dual_emit.py`:

```python
import uuid

import pytest
from structlog.testing import capture_logs

from src.services import activity_service


class _FakeDB:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass


@pytest.mark.asyncio
async def test_log_activity_writes_db_and_emits_event():
    db = _FakeDB()
    with capture_logs() as logs:
        await activity_service.log_activity(
            db,
            action="workspace_created",
            target_type="workspace",
            target_id=uuid.uuid4(),
            actor_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            detail={"name": "Acme"},
        )
    assert db.added  # DB row still created
    audit = [e for e in logs if e["event"] == "audit.activity"]
    assert audit
    assert audit[0]["category"] == "audit"
    assert audit[0]["action"] == "workspace_created"
    assert audit[0]["target_type"] == "workspace"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_audit_dual_emit.py -v`
Expected: FAIL — no `audit.activity` event.

- [ ] **Step 3: Add the dual-emit**

In `service/src/services/activity_service.py`, add the import:

```python
from src.logging_events import log_audit
```

At the end of `log_activity`, after `await db.flush()` and before `return entry`:

```python
    db.add(entry)
    await db.flush()
    log_audit(
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        actor=str(actor_id) if actor_id else "system",
        workspace_id=str(workspace_id) if workspace_id else None,
        detail=detail,
    )
    return entry
```

(The PII processor scrubs `detail`; DB stays the queryable system-of-record.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd service && uv run pytest tests/test_audit_dual_emit.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
make fmt
git add service/src/services/activity_service.py service/tests/test_audit_dual_emit.py
git commit -m "feat(logging): dual-emit audit.activity from log_activity (DB + log stream)"
```

---

### Task 9: Retrofit existing call sites + fix PII leaks

**Files:**
- Modify: `service/src/api/authz_routes.py`, `service/src/api/auth_routes.py`, `service/src/api/admin_routes.py`, `service/src/api/permission_routes.py`, `service/src/middleware/cors.py`, `service/src/main.py`
- Test: `service/tests/test_no_raw_pii_logging.py`

**Interfaces:**
- Consumes: `log_security`, `log_audit` (Task 4), `bind_identity` (Task 5)

**Retrofit pattern** (apply to every existing `logger.*` call):
1. Use a vocabulary event name (`domain.object.action`).
2. Security events (auth/authz failures, denials) → `log_security(event, outcome=…, reason=…, **fields)`.
3. Audit/info → keep `logger.info(event, …)` or `log_audit(...)` if state-changing.
4. **Replace any `email=<raw>` with `email_domain=<value>`** (the processor also backstops this).
5. Where identity is known, call `bind_identity(request, actor=…, workspace_id=…, caller_service=…)`.

- [ ] **Step 1: Write the regression test for the PII leaks**

Create `service/tests/test_no_raw_pii_logging.py`:

```python
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"


def test_authz_routes_log_no_raw_email():
    text = (SRC / "api" / "authz_routes.py").read_text()
    # The two known leaks (email_conflict ~:259, inactive_user ~:267) must be gone.
    assert "email=idp_claims" not in text
    assert "email=user.email" not in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_no_raw_pii_logging.py -v`
Expected: FAIL — both raw-email patterns still present.

- [ ] **Step 3: Fix the two PII leaks in `authz_routes.py`**

At the `authz_resolve_email_conflict` site (~line 256-260):

```python
        log_security(
            "authz.idp.email_conflict",
            outcome="denied",
            reason="email_conflict",
            provider=body.provider,
            email_domain=idp_claims["email"].rsplit("@", 1)[-1],
            caller_service=service_ctx.service_name,
        )
```

At the `authz_resolve_inactive_user` site (~line 264-268):

```python
        log_security(
            "authz.token.denied",
            outcome="denied",
            reason="inactive_user",
            actor=str(user.id),
        )
```

Add `from src.logging_events import log_security` and `from src.middleware.request_context import bind_identity` at the top of `authz_routes.py`.

- [ ] **Step 4: Normalize the remaining `authz_routes.py` sites and bind identity**

Convert these existing call sites (file:line from the audit) to the vocabulary:

- `:170` `github_code_exchange_failed` → `log_security("auth.idp.exchange_failed", outcome="failure", reason="github_code_exchange", provider="github", status=...)`
- `:222-227` `authz_resolve_idp_validation_failed` → `log_security("authz.idp.validation_failed", outcome="failure", reason="idp_validation", provider=body.provider, caller_service=service_ctx.service_name)`
- `:235-239` `authz_resolve_org_not_permitted` → `log_security("authz.token.denied", outcome="denied", reason="org_not_permitted", actor=str(user.id), workspace_id=str(body.workspace_id))`
- `:303-307` `authz_resolve_not_member` → `log_security("authz.token.denied", outcome="denied", reason="not_member", actor=str(user.id), workspace_id=str(body.workspace_id))`
- `:317-321` `authz_resolve_org_not_allowed` → `log_security("authz.token.denied", outcome="denied", reason="org_not_allowed", actor=str(user.id), workspace_id=str(body.workspace_id))`
- `:335-342` `authz_token_issued` → `log_security("authz.token.issued", outcome="success", actor=str(user.id), workspace_id=str(workspace.id), workspace_role=membership.role, caller_service=service_ctx.service_name, actions_count=len(actions))`

On the success path (just before issuing the token), add:
```python
    bind_identity(request, actor=str(user.id), workspace_id=str(workspace.id), caller_service=service_ctx.service_name)
```
If the handler lacks a `request: Request` parameter, add it to the signature (Starlette injects it).

- [ ] **Step 5: Normalize the other files**

- `service/src/api/auth_routes.py:304` `logger.error("auth callback error", …)` → `logger.error("app.error.unhandled", category="app", error=str(e), exc_info=True)`. (Two other error sites in this file: same `app.error.*` treatment.)
- `service/src/api/admin_routes.py:923` `permissions_purged` (and the other 7 sites) → audit/security per kind; **drop the raw `actor=admin["sub"]` only if it is an email** — it is a UUID, so keep as `actor=admin["sub"]`. Convert `logger.error(...)` exception sites to `app.error.unhandled`. In `require_admin`-guarded admin handlers, add `bind_identity(request, actor=admin["sub"])` at entry where a `request` is available.
- `service/src/api/permission_routes.py:163-170` (and `:188-193`, `:214-220`, `:242-247`) → these mirror DB audit actions; leave them as `logger.info` with vocabulary names OR delegate to `log_audit`. Prefer relying on Task 8's dual-emit and converting these to `log_audit(...)` to avoid duplicate lines.
- `service/src/middleware/cors.py:49` `cors_origins_refreshed` → `logger.info("app.cors.refreshed", category="app", count=len(origins))`.
- `service/src/main.py` remaining sites: the security-config warnings/criticals (`:106-140`) → `logger.warning("app.config.insecure", category="app", reason="redis_no_tls", ...)` etc., and the `logger.critical(e)` loop (`:118-119`) → `logger.critical("app.config.insecure", category="app", reason=e)`. Shutdown (`:144`) → `logger.info("app.shutdown")`.

- [ ] **Step 6: Run the PII regression + full suite**

Run: `cd service && uv run pytest tests/test_no_raw_pii_logging.py -v`
Expected: PASS

Run: `cd service && uv run pytest -q`
Expected: PASS (no regressions across the suite).

Run: `cd service && grep -rnE "email=(user\.email|idp_claims|[^_])" src/ || echo "no raw email= logging found"`
Expected: prints `no raw email= logging found` (or only `email_domain=` matches).

- [ ] **Step 7: Commit**

```bash
make fmt
git add service/src
git commit -m "refactor(logging): retrofit call sites to envelope; fix raw-email PII leaks"
```

---

## Phase 4 — Admin SPA

### Task 10: Backend client-log ingest endpoint

**Files:**
- Create: `service/src/api/client_log_routes.py`
- Modify: `service/src/main.py` (`app.include_router(client_log_router)`)
- Test: `service/tests/test_client_log_ingest.py`

**Interfaces:**
- Consumes: `require_admin` (from `src.api.dependencies`), `limiter` (rate_limit), `redact_mapping` (Task 2), `get_client_ip`
- Produces: `POST /internal/client-logs` → 202; re-emits each event as `client.*`

- [ ] **Step 1: Write the failing test**

Create `service/tests/test_client_log_ingest.py`:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from src.api.client_log_routes import router as client_log_router
from src.api.dependencies import require_admin

_XRW = {"X-Requested-With": "XMLHttpRequest"}


def _app():
    app = FastAPI()
    app.include_router(client_log_router)
    app.dependency_overrides[require_admin] = lambda: {"sub": "admin-1", "admin": True}
    return app


def test_accepts_and_reemits():
    with capture_logs() as logs:
        r = TestClient(_app()).post(
            "/internal/client-logs",
            json={"events": [{"event": "client.login.failed", "level": "warning", "fields": {"reason": "bad"}}]},
            headers=_XRW,
        )
    assert r.status_code == 202
    ev = [e for e in logs if e["event"] == "client.login.failed"]
    assert ev
    assert ev[0]["category"] == "security"
    assert ev[0]["client_origin"] is True


def test_rejects_non_client_event_name():
    r = TestClient(_app()).post(
        "/internal/client-logs",
        json={"events": [{"event": "auth.login.failed", "level": "info", "fields": {}}]},
        headers=_XRW,
    )
    assert r.status_code == 422


def test_rejects_oversized_batch():
    big = {"events": [{"event": "client.x", "level": "info", "fields": {}} for _ in range(200)]}
    r = TestClient(_app()).post("/internal/client-logs", json=big, headers=_XRW)
    assert r.status_code == 422


def test_redacts_client_supplied_pii():
    with capture_logs() as logs:
        TestClient(_app()).post(
            "/internal/client-logs",
            json={"events": [{"event": "client.error", "level": "error", "fields": {"email": "a@acme.com"}}]},
            headers=_XRW,
        )
    ev = [e for e in logs if e["event"] == "client.error"][0]
    assert "email" not in ev
    assert ev.get("email_domain") == "acme.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_client_log_ingest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.api.client_log_routes'`

- [ ] **Step 3: Write the implementation**

Create `service/src/api/client_log_routes.py`:

```python
"""Authenticated ingest for admin-SPA client logs. Client input is untrusted:
bounded, allowlisted, and PII-redacted before re-emitting into the log stream.
"""

import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator

from src.api.dependencies import require_admin
from src.logging_redaction import redact_mapping
from src.middleware.rate_limit import get_client_ip, limiter

router = APIRouter(prefix="/internal", tags=["internal"])

_VALID_LEVELS = {"debug", "info", "warning", "error"}
_SECURITY_EVENTS = {"client.login.failed", "client.auth.denied"}


class ClientEvent(BaseModel):
    event: str = Field(max_length=80)
    level: str = "info"
    fields: dict = Field(default_factory=dict)

    @field_validator("event")
    @classmethod
    def _client_namespaced(cls, v: str) -> str:
        if not v.startswith("client."):
            raise ValueError("event must be client.*-namespaced")
        return v

    @field_validator("level")
    @classmethod
    def _known_level(cls, v: str) -> str:
        if v not in _VALID_LEVELS:
            raise ValueError("invalid level")
        return v

    @field_validator("fields")
    @classmethod
    def _bounded_fields(cls, v: dict) -> dict:
        if len(v) > 20:
            raise ValueError("too many fields")
        return v


class ClientLogBatch(BaseModel):
    events: list[ClientEvent] = Field(max_length=50)


@router.post("/client-logs", status_code=202)
@limiter.limit("60/minute")
async def ingest_client_logs(
    request: Request,
    batch: ClientLogBatch,
    admin=Depends(require_admin),
):
    log = structlog.get_logger()
    source_ip = get_client_ip(request)
    for ev in batch.events:
        category = "security" if ev.event in _SECURITY_EVENTS else "app"
        payload = redact_mapping(ev.fields)
        getattr(log, ev.level)(
            ev.event,
            category=category,
            client_origin=True,
            actor=admin.get("sub"),
            source_ip=source_ip,
            **payload,
        )
    return {"accepted": len(batch.events)}
```

> If `redact_mapping` collides with a reserved structlog kwarg (e.g. a client sends `event`/`level`), drop those keys in `_bounded_fields` (add `for k in ("event", "level", "category"): v.pop(k, None)`).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd service && uv run pytest tests/test_client_log_ingest.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Register the router**

In `service/src/main.py`, with the other `include_router` calls (~line 189):

```python
from src.api.client_log_routes import router as client_log_router
...
app.include_router(client_log_router)
```

- [ ] **Step 6: Commit**

```bash
make fmt
git add service/src/api/client_log_routes.py service/src/main.py service/tests/test_client_log_ingest.py
git commit -m "feat(logging): authenticated /internal/client-logs ingest for admin-SPA events"
```

---

### Task 11: Admin SPA client logger

**Files:**
- Create: `admin/src/lib/logger.ts`, `admin/src/components/ErrorBoundary.tsx`
- Modify: `admin/src/api/client.ts`, `admin/src/main.tsx`, `admin/src/pages/Login.tsx`

**Interfaces:**
- Produces: `clientLog(event, level, fields?)`, `getLastRequestId()`
- No frontend test runner exists; this task is verified by `tsc -b` (typecheck) + `eslint` + a manual smoke.

- [ ] **Step 1: Create the client logger**

Create `admin/src/lib/logger.ts`:

```typescript
type Level = "debug" | "info" | "warning" | "error";
type Fields = Record<string, unknown>;

interface ClientEvent {
  event: string;
  level: Level;
  fields: Fields;
}

const BASE = "/api";
const FLUSH_MS = 5000;
const MAX_BUFFER = 50;

let buffer: ClientEvent[] = [];
let timer: ReturnType<typeof setTimeout> | null = null;
let lastRequestId: string | null = null;

export function setLastRequestId(id: string | null): void {
  if (id) lastRequestId = id;
}

export function getLastRequestId(): string | null {
  return lastRequestId;
}

export function clientLog(event: string, level: Level, fields: Fields = {}): void {
  if (!event.startsWith("client.")) event = `client.${event}`;
  if (import.meta.env.DEV) {
    // eslint-disable-next-line no-console
    console[level === "warning" ? "warn" : level]?.(event, fields);
  }
  buffer.push({ event, level, fields: { request_id: lastRequestId, ...fields } });
  if (buffer.length >= MAX_BUFFER) {
    void flush();
  } else if (!timer) {
    timer = setTimeout(() => void flush(), FLUSH_MS);
  }
}

async function flush(): Promise<void> {
  if (timer) {
    clearTimeout(timer);
    timer = null;
  }
  if (buffer.length === 0) return;
  const events = buffer.slice(0, MAX_BUFFER);
  buffer = buffer.slice(MAX_BUFFER);
  try {
    await fetch(`${BASE}/internal/client-logs`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify({ events }),
      keepalive: true,
    });
  } catch {
    // best-effort; drop on failure to avoid unbounded growth
  }
}

// Flush on page hide.
if (typeof window !== "undefined") {
  window.addEventListener("pagehide", () => void flush());
}
```

- [ ] **Step 2: Capture `X-Request-ID` and log API errors in `client.ts`**

In `admin/src/api/client.ts`, import the logger and instrument `request()`:

```typescript
import { clientLog, setLastRequestId } from "../lib/logger";
```

Inside `request()`, after `const res = await fetch(...)`:

```typescript
  setLastRequestId(res.headers.get("x-request-id"));
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = body.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((e: { msg?: string }) => e.msg ?? JSON.stringify(e)).join("; ")
          : `HTTP ${res.status}`;
    const event = res.status === 401 || res.status === 403 ? "client.auth.denied" : "client.api.error";
    clientLog(event, res.status >= 500 ? "error" : "warning", { status: res.status, path });
    throw new Error(message);
  }
```

(Do not log the `/internal/client-logs` path itself — guard with `if (!path.startsWith("/internal/client-logs"))` to avoid feedback loops.)

- [ ] **Step 3: Add an ErrorBoundary**

Create `admin/src/components/ErrorBoundary.tsx`:

```typescript
import { Component, type ErrorInfo, type ReactNode } from "react";
import { clientLog } from "../lib/logger";

export class ErrorBoundary extends Component<{ children: ReactNode }, { hasError: boolean }> {
  state = { hasError: false };

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    clientLog("client.error.boundary", "error", {
      message: error.message,
      component_stack: info.componentStack?.slice(0, 500),
    });
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex h-screen items-center justify-center bg-zinc-950 text-zinc-300">
          Something went wrong. Please reload.
        </div>
      );
    }
    return this.props.children;
  }
}
```

- [ ] **Step 4: Wire global handlers + ErrorBoundary in `main.tsx`**

In `admin/src/main.tsx`:

```typescript
import { ErrorBoundary } from "./components/ErrorBoundary";
import { clientLog } from "./lib/logger";

window.addEventListener("error", (e) => {
  clientLog("client.error.unhandled", "error", { message: e.message, source: e.filename });
});
window.addEventListener("unhandledrejection", (e) => {
  clientLog("client.error.rejection", "error", { reason: String(e.reason).slice(0, 300) });
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>
);
```

- [ ] **Step 5: Log failed admin logins in `Login.tsx`**

In `admin/src/pages/Login.tsx`, inside the existing `if (errorCode && errorMessages[errorCode]) { ... }` block, add:

```typescript
import { clientLog } from "../lib/logger";
...
    clientLog("client.login.failed", "warning", { reason: errorCode });
```

- [ ] **Step 6: Verify (typecheck + lint + smoke)**

Run: `cd admin && npm run build`
Expected: `tsc -b` passes with no type errors; Vite build succeeds.

Run: `cd admin && npm run lint`
Expected: no eslint errors.

Manual smoke (optional, if backend running): load the admin UI, trigger a failed action, confirm a `POST /api/internal/client-logs` fires in the Network tab and the backend emits a `client.*` JSON line.

- [ ] **Step 7: Commit**

```bash
git add admin/src/lib/logger.ts admin/src/components/ErrorBoundary.tsx admin/src/api/client.ts admin/src/main.tsx admin/src/pages/Login.tsx
git commit -m "feat(admin): structured client logging + error boundary -> /internal/client-logs"
```

---

## Phase 5 — Docs & rollout

### Task 12: Documentation, env example, rollout

**Files:**
- Create: `docs/observability/logging.md`
- Modify: `mkdocs.yml` (nav), the service env example, deployment docs

- [ ] **Step 1: Write the logging doc**

Create `docs/observability/logging.md` documenting: the canonical envelope (table from spec §5), the event vocabulary (table from spec §6), the PII policy ("log `email_domain`, never `email`"), the config flags, and the "what anomalies this enables" section (spec §10). Include a sample JSON line.

- [ ] **Step 2: Add to mkdocs nav**

In `mkdocs.yml`, add under an "Observability" (or existing deployment) nav section:

```yaml
  - Observability:
      - Logging: observability/logging.md
```

- [ ] **Step 3: Add config flags to the env example**

Run: `grep -rl "RATE_LIMIT_RPM\|SESSION_SECRET_KEY" --include="*.example" --include="*.env*" /Users/sidx/workspace/identity-service` to locate the env example file, then append:

```bash
# Logging
LOG_LEVEL=INFO          # DEBUG|INFO|WARNING|ERROR|CRITICAL
LOG_FORMAT=json         # json (prod) | console (dev)
LOG_PII_REDACTION=true  # mask emails/secrets in logs
```

- [ ] **Step 4: Note rollout in deployment docs**

In the deployment docs (e.g. `docs/deployment/…`), add a short note: prod uses `LOG_FORMAT=json` to stdout (collected by the swarm log driver); no DB migration is required; the DB `ActivityLog` table is unchanged and remains the audit system-of-record.

- [ ] **Step 5: Verify docs build (strict)**

Run: `cd /Users/sidx/workspace/identity-service && uv run mkdocs build --strict`
Expected: builds with no warnings (the docs.yml CI gate). If `mkdocs` is not in the active env, use the project's documented `make docs-serve` toolchain.

- [ ] **Step 6: Commit**

```bash
git add docs/observability/logging.md mkdocs.yml docs/deployment
git commit -m "docs(logging): observability logging guide, event vocabulary, env flags"
```

---

## Final Verification

- [ ] Run the full backend suite: `cd service && uv run pytest -q` — all green.
- [ ] Lint: `make lint` — clean.
- [ ] Boot check: `cd service && uv run python -c "from src.main import app; from fastapi.testclient import TestClient; c=TestClient(app); print(c.get('/health').json())"` — `{"status":"ok"}`, no errors.
- [ ] Admin build: `cd admin && npm run build && npm run lint` — clean.
- [ ] Manual: hit a real endpoint with `LOG_FORMAT=json`, confirm a `http.access` line with `request_id`, and that a forced auth failure emits a `security` event with `outcome`/`reason` and no raw email.
