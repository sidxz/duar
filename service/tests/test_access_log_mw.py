from fastapi import FastAPI
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from src.middleware.access_log import AccessLogMiddleware, _route_template


class _FakeRoute:
    def __init__(self, *, endpoint=None, path=None, path_format=None):
        self.endpoint = endpoint
        if path is not None:
            self.path = path
        if path_format is not None:
            self.path_format = path_format


def test_route_template_reads_path_format_then_path():
    # Newer Starlette sets scope["route"] and exposes the template as path_format
    # (route.path is None); older exposes it as path. Be robust to both — the
    # regression that logged "__unmatched__" for every request came from reading
    # only .path against a newer Starlette where it is None.
    newer = _FakeRoute(path_format="/users/{id}")  # 1.x: path absent/None
    assert _route_template({"route": newer}) == "/users/{id}"
    older = _FakeRoute(path="/users/{id}")  # 0.x: only path
    assert _route_template({"route": older}) == "/users/{id}"


def test_route_template_falls_back_to_endpoint_mapping():
    def ep():
        pass

    route = _FakeRoute(endpoint=ep, path_format="/echo/{name}")

    class _App:
        routes = [route]

    # No scope["route"], but scope["endpoint"] + app.routes lets us recover it.
    assert _route_template({"endpoint": ep, "app": _App()}) == "/echo/{name}"


def test_route_template_unmatched_when_nothing_recorded():
    assert _route_template({}) == "__unmatched__"  # 404 / OPTIONS preflight


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
    assert "user_agent" in e


def test_4xx_logged_as_warning():
    with capture_logs() as logs:
        TestClient(_app()).get("/nope")
    e = [x for x in logs if x["event"] == "http.access"][0]
    assert e["http.status"] == 404
    assert e["log_level"] == "warning"


def test_access_log_enriches_from_request_state():
    from starlette.requests import Request
    from src.middleware.request_context import RequestContextMiddleware, bind_identity

    app = FastAPI()
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestContextMiddleware)  # outermost

    @app.get("/who")
    def who(request: Request):
        bind_identity(request, actor="u1", workspace_id="ws1")
        return {"ok": True}

    with capture_logs() as logs:
        TestClient(app).get("/who")
    e = [x for x in logs if x["event"] == "http.access"][0]
    assert e["actor"] == "u1"
    assert e["workspace_id"] == "ws1"


def test_skips_health():
    app = FastAPI()
    app.add_middleware(AccessLogMiddleware)

    @app.get("/health")
    def health():
        return {"ok": True}

    with capture_logs() as logs:
        TestClient(app).get("/health")
    assert not [e for e in logs if e.get("event") == "http.access"]
