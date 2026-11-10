"""The default and aggregate tiers must throttle routes added with
``include_router()``: that is how every API route in ``src.main`` is registered.

Uses fresh in-memory limiters so the suite never touches Redis and never
mutates the module singleton's route registry.
"""

import uuid

import pytest
from fastapi import APIRouter, FastAPI, Request
from slowapi.errors import RateLimitExceeded
from starlette.testclient import TestClient

import src.middleware.rate_limit as rl


def _client(*, default="100/minute", aggregate=None):
    lim = rl._FailOpenLimiter(
        key_func=rl.get_client_ip,
        default_limits=[default],
        application_limits=[aggregate] if aggregate else [],
        storage_uri="memory://",
        swallow_errors=True,
    )
    app = FastAPI()
    app.state.limiter = lim
    app.add_exception_handler(RateLimitExceeded, rl.rate_limit_exceeded_handler)
    app.add_middleware(rl.RateLimitMiddleware)

    router = APIRouter(prefix="/api")
    nested = APIRouter(prefix="/nested")

    @router.get("/plain")
    async def plain():
        return {"ok": True}

    @router.get("/other")
    async def other():
        return {"ok": True}

    @nested.get("/deep")
    async def deep():
        return {"ok": True}

    @router.get("/decorated")
    @lim.limit("5/minute")
    async def decorated(request: Request):
        return {"ok": True}

    @router.get("/exempt")
    @lim.exempt
    async def exempt():
        return {"ok": True}

    @router.get("/items/{item_id}")
    async def item(item_id: str):
        return {"ok": True}

    @router.get("/reports/{name}")
    @lim.limit("2/minute")
    async def report(request: Request, name: str):
        return {"ok": True}

    router.include_router(nested)
    app.include_router(router)
    return TestClient(app)


def _statuses(client, path, n):
    return [client.get(path).status_code for _ in range(n)]


def test_default_tier_throttles_a_router_route():
    c = _client(default="2/minute")
    assert _statuses(c, "/api/plain", 3) == [200, 200, 429]


def test_default_tier_throttles_a_nested_router_route():
    c = _client(default="2/minute")
    assert _statuses(c, "/api/nested/deep", 3) == [200, 200, 429]


def test_default_tier_counts_each_route_separately():
    c = _client(default="2/minute")
    assert _statuses(c, "/api/plain", 2) == [200, 200]
    assert _statuses(c, "/api/other", 2) == [200, 200]


def test_aggregate_tier_counts_across_router_routes():
    c = _client(aggregate="3/minute")
    spread = [
        c.get(p).status_code
        for p in ("/api/plain", "/api/other", "/api/nested/deep", "/api/plain")
    ]
    assert spread == [200, 200, 200, 429]


def test_decorated_route_is_bound_only_by_its_own_limit():
    # The default tier (1/minute) must not also apply: the decorator owns the route.
    c = _client(default="1/minute")
    assert _statuses(c, "/api/decorated", 6) == [200, 200, 200, 200, 200, 429]


def test_exempt_route_is_never_throttled():
    c = _client(default="1/minute")
    assert _statuses(c, "/api/exempt", 3) == [200, 200, 200]


def test_default_tier_counts_a_route_once_whatever_its_path_parameters():
    c = _client(default="2/minute")
    assert [c.get(f"/api/items/{n}").status_code for n in "abc"] == [200, 200, 429]


def test_a_routes_own_limit_counts_it_once_whatever_its_path_parameters():
    c = _client()
    assert [c.get(f"/api/reports/{n}").status_code for n in "abc"] == [200, 200, 429]


def test_the_service_limiter_counts_per_route():
    assert rl.limiter._key_style == "endpoint"


def test_unknown_path_passes_through_unthrottled():
    c = _client(default="1/minute")
    assert _statuses(c, "/api/missing", 3) == [404, 404, 404]


_WORKSPACE = str(uuid.uuid4())


@pytest.mark.parametrize(
    ("method", "path", "want"),
    [
        ("GET", "/users/me", "src.api.user_routes.get_me"),
        ("PATCH", "/users/me", "src.api.user_routes.update_me"),
        ("GET", "/admin/stats", "src.api.admin_routes.get_stats"),
        ("POST", "/auth/refresh", "src.api.auth_routes.refresh_token"),
        (
            "DELETE",
            f"/workspaces/{_WORKSPACE}",
            "src.api.workspace_routes.delete_workspace",
        ),
        # A literal segment registered before a path parameter wins, as in the router.
        ("GET", "/admin/workspaces/all", "src.api.admin_routes.list_all_workspaces"),
        (
            "GET",
            f"/admin/workspaces/{_WORKSPACE}",
            "src.api.admin_routes.get_workspace_detail",
        ),
        ("GET", "/health", "src.main.health"),
        ("POST", "/users/me", None),  # path exists, method does not
        ("GET", "/no-such-route", None),
    ],
)
def test_handler_lookup_on_the_real_app(method, path, want):
    import src.main

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "root_path": "",
        "path_params": {},
        "headers": [],
    }
    handler = rl.find_route_handler(src.main.app.routes, scope)
    got = f"{handler.__module__}.{handler.__name__}" if handler else None
    assert got == want


def test_app_installs_the_rate_limit_middleware():
    import src.main

    assert rl.RateLimitMiddleware in [m.cls for m in src.main.app.user_middleware]
