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
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "query_string": b"",
    }
    req = Request(scope)
    structlog.contextvars.clear_contextvars()
    bind_identity(req, actor="u1", workspace_id="ws1", caller_service=None)
    assert structlog.contextvars.get_contextvars().get("actor") == "u1"
    assert req.scope["state"]["actor"] == "u1"
    assert "caller_service" not in req.scope["state"]  # None dropped
