"""The per-address limits (``address_limits``) on routes added with
``include_router()``, which is how every API route in ``src.main`` is
registered: the default and aggregate tiers on a route without a limit of its
own, and the failed-authentication budget on a route limited per caller.

Uses fresh in-memory limiters so the suite never touches Redis and never
mutates the module singleton's route registry.
"""

import asyncio

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from slowapi.errors import RateLimitExceeded
from starlette.testclient import TestClient
from structlog.testing import capture_logs

import src.middleware.rate_limit as rl
from src.middleware.access_log import AccessLogMiddleware
from src.middleware.request_context import bind_identity


def _client(*, default="100/minute", aggregate=None):
    lim = rl._FailOpenLimiter(
        key_func=rl.get_client_ip,
        default_limits=[default],
        application_limits=[aggregate] if aggregate else [],
        storage_uri="memory://",
        swallow_errors=True,
    )
    app = FastAPI(dependencies=[Depends(rl.address_limits)])
    app.state.limiter = lim
    app.add_exception_handler(RateLimitExceeded, rl.rate_limit_exceeded_handler)
    app.add_middleware(AccessLogMiddleware)

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


def test_a_refusal_is_logged_under_the_route_not_the_url():
    c = _client(default="1/minute")
    with capture_logs() as logs:
        assert [c.get(f"/api/items/{n}").status_code for n in "ab"] == [200, 429]
    refused = [e for e in logs if e.get("http.status") == 429]
    refused += [e for e in logs if e["event"] == "ratelimit.exceeded"]
    assert [e["event"] for e in refused] == ["http.access", "ratelimit.exceeded"]
    assert {e["http.route"] for e in refused} == {"/api/items/{item_id}"}


# --- failed authentication on a route limited per caller ---------------------


def _guarded_client(*, aggregate="3/minute"):
    """``/api/me`` is limited per user behind a credential check; ``/api/login``
    is limited per address and takes no credential."""
    lim = rl._FailOpenLimiter(
        key_func=rl.get_client_ip,
        application_limits=[aggregate] if aggregate else [],
        storage_uri="memory://",
        swallow_errors=True,
    )
    app = FastAPI(dependencies=[Depends(rl.address_limits)])
    app.state.limiter = lim
    app.add_exception_handler(RateLimitExceeded, rl.rate_limit_exceeded_handler)
    router = APIRouter(prefix="/api")

    async def signed_in(request: Request):
        # Stands in for the auth dependencies: binds what they bind, or rejects.
        await asyncio.sleep(0)  # they await the database: requests interleave
        if key := request.headers.get("X-Service-Key"):
            if key != "good-key":
                raise HTTPException(401, "Invalid service key")
            bind_identity(request, caller_service="notes")
            request.state.service_key_proven = True
        elif request.headers.get("Origin"):
            bind_identity(request, caller_service="notes")
        user = request.headers.get("X-User")
        if not user:
            raise HTTPException(401, "Not authenticated")
        if user == "outsider":
            raise HTTPException(403, "Not a member")
        bind_identity(request, actor=user)

    @router.get("/me")
    @lim.limit("100/minute", key_func=rl.user_or_ip_key)
    async def me(request: Request, _: None = Depends(signed_in)):
        return {"ok": True}

    @router.get("/late")
    @lim.limit("100/minute", key_func=rl.user_or_ip_key)
    async def late(request: Request):
        raise HTTPException(401, "Rejected in the handler")

    @router.get("/login")
    @lim.limit("100/minute")
    async def login(request: Request):
        raise HTTPException(401, "Bad code")

    @router.get("/plain")
    async def plain():
        return {"ok": True}

    app.include_router(router)
    return TestClient(app)


def _me(client, n, **headers):
    return [client.get("/api/me", headers=headers).status_code for _ in range(n)]


def test_failed_authentication_is_limited_per_address():
    c = _guarded_client()
    with capture_logs() as logs:
        assert _me(c, 4) == [401, 401, 401, 429]
    (event,) = [e for e in logs if e["event"] == "ratelimit.exceeded"]
    assert event["reason"] == "failed_auth"
    assert event["http.route"] == "/api/me"


def test_failed_authentication_is_counted_per_client_address(monkeypatch):
    monkeypatch.setattr(rl.settings, "behind_proxy", True)
    monkeypatch.setattr(rl.settings, "trusted_proxy_count", 1)
    c = _guarded_client()
    assert _me(c, 4, **{"X-Forwarded-For": "203.0.113.1"}) == [401, 401, 401, 429]
    assert _me(c, 1, **{"X-Forwarded-For": "203.0.113.2"}) == [401]


def test_each_listener_counts_failed_authentication_on_its_own():
    c = _guarded_client()
    c.app.state.tier = "internal"
    assert _me(c, 4) == [401, 401, 401, 429]
    c.app.state.tier = "public"
    assert _me(c, 1) == [401]


def test_a_401_raised_by_the_handler_counts_too():
    c = _guarded_client()
    assert _statuses(c, "/api/late", 4) == [401, 401, 401, 429]


def test_valid_credentials_get_through_whatever_else_came_from_their_address():
    c = _guarded_client()
    assert _me(c, 5) == [401, 401, 401, 429, 429]
    assert _me(c, 2, **{"X-User": "alice"}) == [200, 200]
    assert _me(c, 1, **{"X-Service-Key": "good-key", "X-User": "alice"}) == [200]


@pytest.mark.asyncio
async def test_a_burst_of_failures_does_not_slip_under_the_limit():
    app = _guarded_client().app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        responses = await asyncio.gather(*(c.get("/api/me") for _ in range(20)))
    assert sorted(r.status_code for r in responses) == [401] * 3 + [429] * 17


def test_only_a_401_counts_as_failed_authentication():
    c = _guarded_client()
    assert _me(c, 5, **{"X-User": "outsider"}) == [403] * 5
    assert _me(c, 2) == [401, 401]  # the budget is still whole


def test_failed_authentication_is_counted_apart_from_the_aggregate_tier():
    c = _guarded_client()
    assert _statuses(c, "/api/plain", 3) == [200, 200, 200]  # the aggregate, used up
    assert _me(c, 3) == [401, 401, 401]  # and still three failures to go
    c = _guarded_client()
    assert _me(c, 3) == [401, 401, 401]
    assert _statuses(c, "/api/plain", 3) == [200, 200, 200]


def test_an_authenticated_request_does_not_count_per_address():
    c = _guarded_client()
    assert _me(c, 5, **{"X-User": "alice"}) == [200] * 5
    assert _me(c, 2) == [401, 401]  # the budget is still whole


def test_a_wrong_service_key_counts_as_failed_authentication():
    c = _guarded_client()
    assert _me(c, 4, **{"X-Service-Key": "guess"}) == [401, 401, 401, 429]


def test_a_backends_rejected_user_token_does_not_count_against_its_address():
    c = _guarded_client()
    assert _me(c, 5, **{"X-Service-Key": "good-key"}) == [401] * 5


def test_a_matching_origin_alone_does_not_stand_in_for_a_service_key():
    c = _guarded_client()
    assert _me(c, 4, Origin="https://notes.example") == [401, 401, 401, 429]


def test_a_route_limited_per_address_is_bound_only_by_its_own_limit():
    c = _guarded_client()
    assert _statuses(c, "/api/login", 5) == [401] * 5


def test_failed_authentication_is_not_limited_when_the_aggregate_is_off():
    c = _guarded_client(aggregate="")
    assert _me(c, 5) == [401] * 5
