"""slowapi keying: authenticated routes bucket per user/service, not per IP, so
one busy actor (or a shared NAT/proxy IP) can't throttle everyone else. Also
asserts the live limiter is configured fail-open.

Uses fresh in-memory Limiters so the suite never touches Redis and never mutates
the module singleton's route registry.
"""

from fastapi import Depends, FastAPI, Request
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.testclient import TestClient

import src.middleware.rate_limit as rl


def _client(*, key_func, identity_header, field):
    lim = Limiter(
        key_func=rl.get_client_ip, storage_uri="memory://", swallow_errors=True
    )
    app = FastAPI()
    app.state.limiter = lim
    app.add_exception_handler(RateLimitExceeded, rl.rate_limit_exceeded_handler)

    async def bind(request: Request):
        # Mirror bind_identity(): write the identity into scope state, which
        # request.state reads, BEFORE the decorator's check runs in the wrapper.
        val = request.headers.get(identity_header)
        if val:
            request.scope.setdefault("state", {})[field] = val

    @app.get("/r")
    @lim.limit("2/minute", key_func=key_func)
    async def r(request: Request, _: None = Depends(bind)):
        return {"ok": True}

    return TestClient(app)


def test_per_user_keying_isolates_users():
    c = _client(key_func=rl.user_or_ip_key, identity_header="X-Actor", field="actor")
    assert c.get("/r", headers={"X-Actor": "alice"}).status_code == 200
    assert c.get("/r", headers={"X-Actor": "alice"}).status_code == 200
    blocked = c.get("/r", headers={"X-Actor": "alice"})
    assert blocked.status_code == 429  # alice exhausted
    assert (
        int(blocked.headers["Retry-After"]) > 0
    )  # handler sources Retry-After from the limit window
    assert c.get("/r", headers={"X-Actor": "bob"}).status_code == 200  # bob unaffected


def test_per_service_keying_isolates_services():
    c = _client(
        key_func=rl.service_or_ip_key,
        identity_header="X-Service",
        field="caller_service",
    )
    assert c.get("/r", headers={"X-Service": "svc-a"}).status_code == 200
    assert c.get("/r", headers={"X-Service": "svc-a"}).status_code == 200
    assert c.get("/r", headers={"X-Service": "svc-a"}).status_code == 429
    assert c.get("/r", headers={"X-Service": "svc-b"}).status_code == 200


def test_falls_back_to_ip_without_identity():
    c = _client(key_func=rl.user_or_ip_key, identity_header="X-Actor", field="actor")
    assert c.get("/r").status_code == 200
    assert c.get("/r").status_code == 200
    assert c.get("/r").status_code == 429  # same TestClient IP shares one bucket


def test_singleton_limiter_is_fail_open():
    # A Redis blip must let traffic through, not 500 or throttle.
    assert rl.limiter._swallow_errors is True


def test_route_keying_is_wired():
    # Importing main registers every router, applying the decorators onto the
    # module-singleton limiter's route registry.
    import src.main  # noqa: F401

    # route name -> set of key_funcs on its limits.
    by_route = {
        name: {lim.key_func for lim in lims}
        for name, lims in rl.limiter._route_limits.items()
    }

    # Service-key-authed routes bind caller_service (never actor), so they MUST
    # key per calling service — user_or_ip_key would silently degrade to per-IP.
    for name in (
        "src.api.authz_routes.resolve",
        "src.api.permission_routes.get_enriched_resource_acl",
    ):
        assert rl.service_or_ip_key in by_route[name], name
        assert rl.user_or_ip_key not in by_route[name], name

    # User-JWT / admin-cookie routes bind actor, so they MUST key per user.
    for name in (
        "src.api.workspace_routes.list_members",
        "src.api.group_routes.list_group_members",
        "src.api.client_log_routes.ingest_client_logs",
        "src.api.admin_routes.bulk_user_status",
        "src.api.admin_routes.rotate_service_app_key",
        "src.api.org_admin_routes.create_organization",
    ):
        assert rl.user_or_ip_key in by_route[name], name


def test_fail_open_when_storage_unreachable():
    # swallow_errors alone is not enough: slowapi leaves request.state.view_rate_limit
    # unset on a swallowed storage error, and header injection then AttributeErrors
    # -> HTTP 500. _FailOpenLimiter pre-seeds the attribute so a Redis outage passes
    # traffic through (with the real response) on BOTH the undecorated (middleware)
    # and decorated (endpoint-wrapper) paths.
    from slowapi.middleware import SlowAPIASGIMiddleware

    dead_redis = "redis://127.0.0.1:6390/0"  # nothing listening -> connection refused
    lim = rl._FailOpenLimiter(
        key_func=rl.get_client_ip,
        default_limits=["120/minute"],
        application_limits=["300/minute"],
        storage_uri=dead_redis,
        swallow_errors=True,
        enabled=True,
    )
    app = FastAPI()
    app.state.limiter = lim
    app.add_exception_handler(RateLimitExceeded, rl.rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIASGIMiddleware)

    @app.get("/u")
    async def u(request: Request):
        return {"ok": True}

    @app.get("/d")
    @lim.limit("10/minute")
    async def d(request: Request):
        return {"ok": True}

    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/u").status_code == 200  # undecorated path fails open, not 500
    assert c.get("/d").status_code == 200  # decorated path fails open, not 500


def test_rate_limit_report_reflects_live_settings(monkeypatch):
    monkeypatch.setattr(rl.settings, "rate_limit_read", "77/minute")
    report = rl.rate_limit_report()
    read = next(r for r in report if "reads" in r["endpoint"])
    assert read["limit"] == "77/minute"


def test_rate_limit_report_shows_disabled_aggregate(monkeypatch):
    monkeypatch.setattr(rl.settings, "rate_limit_aggregate", "")
    report = rl.rate_limit_report()
    agg = next(r for r in report if r["endpoint"].startswith("aggregate"))
    assert agg["limit"] == "disabled"
