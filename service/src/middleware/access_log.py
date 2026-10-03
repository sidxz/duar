"""Single structured access-log event per request. Pure ASGI, sits outermost."""

import time

from starlette.requests import Request

from src.logging_events import log_access
from src.middleware.rate_limit import get_client_ip

_SKIP_PATHS = {
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/.well-known/jwks.json",
}

# Placeholder for requests that matched no route at all (404, and OPTIONS CORS
# preflights, which the CORS middleware answers without ever invoking the
# router). We never log the raw path for these: it can embed PII (e.g.
# /users/by-email/alice@acme.com) and explodes http.route cardinality, which
# violates the "route template, never raw URL" contract in
# docs/observability/logging.md. (A 405 method-mismatch is a PARTIAL match, for
# which Starlette still sets scope["endpoint"], so those log their real low-
# cardinality template — which is fine.)
_UNMATCHED = "__unmatched__"


def _route_template(scope) -> str:
    """Low-cardinality route template (e.g. /echo/{name}).

    Reads the route the router recorded on the scope during routing (no regex
    re-match) and returns its template. Version-robust across the framework's
    routing reworks: prefers ``scope["route"]`` (newer Starlette sets it) and
    reads ``path_format`` (newer) or ``path`` (older); falls back to mapping the
    recorded ``scope["endpoint"]`` back to its route. Unmatched requests (404 /
    OPTIONS preflight) resolve to a constant placeholder rather than leaking the
    raw path (which can carry PII and explode http.route cardinality).
    """
    route = scope.get("route")
    if route is None:
        endpoint = scope.get("endpoint")
        if endpoint is not None:
            for r in getattr(scope.get("app"), "routes", []):
                if getattr(r, "endpoint", None) is endpoint:
                    route = r
                    break
    if route is not None:
        return (
            getattr(route, "path_format", None)
            or getattr(route, "path", None)
            or _UNMATCHED
        )
    return _UNMATCHED


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
                level = (
                    "info" if status < 400 else "warning" if status < 500 else "error"
                )
                req = Request(scope)
                fields = {
                    "http.method": scope.get("method"),
                    "http.route": _route_template(scope),
                    "http.status": status,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                    "resp_bytes": state["bytes"],
                    "source_ip": get_client_ip(req),
                    "user_agent": req.headers.get("user-agent", ""),
                }
                state_obj = scope.get("state")
                for k in ("actor", "workspace_id", "caller_service"):
                    v = (
                        state_obj.get(k)
                        if isinstance(state_obj, dict)
                        else getattr(state_obj, k, None)
                    )
                    if v is not None:
                        fields[k] = v
                log_access("http.access", level=level, **fields)
            except Exception:
                pass  # never let logging break the response
