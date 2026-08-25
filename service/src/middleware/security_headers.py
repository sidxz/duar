from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send


class MaxBodySizeMiddleware:
    """Reject requests whose body exceeds the configured maximum.

    Checks Content-Length header (fast path) and also wraps the ASGI receive
    callable to enforce the limit on the actual byte stream, catching chunked
    or streaming uploads that omit or lie about Content-Length.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = 10_485_760):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Fast path: reject if Content-Length header exceeds limit
        for header_name, header_value in scope.get("headers", []):
            if header_name == b"content-length":
                try:
                    if int(header_value) > self.max_bytes:
                        await self._send_413(send)
                        return
                except ValueError:
                    await self._send_400(send)
                    return
                break

        # Wrap receive to enforce on actual streamed bytes
        bytes_received = 0
        rejected = False

        async def limiting_receive() -> dict:
            nonlocal bytes_received, rejected
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                bytes_received += len(body)
                if bytes_received > self.max_bytes:
                    rejected = True
                    # Return empty body to stop the handler, we'll send 413
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        response_started = False
        original_send = send

        async def guarded_send(message: dict) -> None:
            nonlocal response_started
            if rejected and not response_started:
                # Suppress the app's response; we'll send 413 instead
                return
            if message["type"] == "http.response.start":
                response_started = True
            await original_send(message)

        await self.app(scope, limiting_receive, guarded_send)

        if rejected and not response_started:
            await self._send_413(send)

    @staticmethod
    async def _send_413(send: Send) -> None:
        body = b'{"detail":"Request body too large"}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    [b"content-type", b"application/json"],
                    [b"content-length", str(len(body)).encode()],
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _send_400(send: Send) -> None:
        body = b'{"detail":"Invalid Content-Length header"}'
        await send(
            {
                "type": "http.response.start",
                "status": 400,
                "headers": [
                    [b"content-type", b"application/json"],
                    [b"content-length", str(len(body)).encode()],
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add standard security headers to every response."""

    def __init__(self, app, hsts: bool = False):
        super().__init__(app)
        self.hsts = hsts

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        # Prevent caching of sensitive auth/admin responses
        path = request.url.path
        if (
            path.startswith("/auth")
            or path.startswith("/admin")
            or path.startswith("/users")
            or path.startswith("/onboard")  # one-time invite links, personal data
        ):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        # `form-action`/`base-uri` are no-fallback directives: `default-src 'none'` does NOT
        # cover them, so they must be pinned explicitly. HTML pages may submit plain forms
        # to Duar itself ('self') — the hosted /onboard pages need it; error pages have no
        # forms so it is harmless there. (`style-src 'unsafe-inline'` is a deliberate
        # trade-off so the rendered HTML can use inline styles.)
        _HTML_CSP = (
            "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; "
            "frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        )
        csp_override = response.headers.get("X-CSP-Override")
        if csp_override == "html-page":
            response.headers["Content-Security-Policy"] = _HTML_CSP
            del response.headers["X-CSP-Override"]
        else:
            if csp_override is not None:
                del response.headers["X-CSP-Override"]
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; frame-ancestors 'none'; form-action 'none'; base-uri 'none'"
            )
        response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
        response.headers["Server"] = "daikon"
        if self.hsts:
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains; preload"
            )
        return response
