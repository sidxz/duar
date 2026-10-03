"""Per-request correlation. Pure ASGI (NOT BaseHTTPMiddleware) so contextvars
bound here propagate into the route handler.
"""

import uuid

import structlog

REQUEST_ID_HEADER = "x-request-id"
_MAX_ID_LEN = 64


def _valid_request_id(value: str) -> bool:
    return (
        bool(value) and len(value) <= _MAX_ID_LEN and value.replace("-", "").isalnum()
    )


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
