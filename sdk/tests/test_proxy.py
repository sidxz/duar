"""Tests for the browser→Duar reverse-proxy router (private-network deployments)."""

import uuid

import respx
from fastapi import FastAPI
from httpx import Response
from starlette.testclient import TestClient

from duar_auth.proxy import create_proxy_router

DUAR = "http://duar:9003"


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(create_proxy_router(DUAR, service_key="sk_test"), prefix="/api/duar")
    return app


class TestResolveProxy:
    @respx.mock
    def test_injects_service_key_and_passes_body_through(self):
        route = respx.post(f"{DUAR}/authz/resolve").mock(return_value=Response(200, json={"authz_token": "eyJ..."}))
        client = TestClient(_make_app())
        resp = client.post(
            "/api/duar/authz/resolve",
            json={"idp_token": "idp", "provider": "google", "workspace_id": "w1"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"authz_token": "eyJ..."}
        sent = route.calls.last.request
        assert sent.headers["x-service-key"] == "sk_test"
        assert b'"workspace_id": "w1"' in sent.content or b'"workspace_id":"w1"' in sent.content

    @respx.mock
    def test_forwards_xff_and_user_agent(self):
        route = respx.post(f"{DUAR}/authz/resolve").mock(return_value=Response(200, json={}))
        client = TestClient(_make_app())
        client.post(
            "/api/duar/authz/resolve",
            json={"idp_token": "idp", "provider": "google"},
            headers={
                "X-Forwarded-For": "203.0.113.7",
                "User-Agent": "TestBrowser/1.0",
            },
        )
        sent = route.calls.last.request
        assert sent.headers["x-forwarded-for"] == "203.0.113.7"
        assert sent.headers["user-agent"] == "TestBrowser/1.0"

    @respx.mock
    def test_upstream_error_status_passes_through(self):
        respx.post(f"{DUAR}/authz/resolve").mock(return_value=Response(403, json={"detail": "not a member"}))
        client = TestClient(_make_app())
        resp = client.post("/api/duar/authz/resolve", json={"idp_token": "x", "provider": "google"})
        assert resp.status_code == 403
        assert resp.json()["detail"] == "not a member"

    @respx.mock
    def test_unreachable_upstream_returns_502(self):
        import httpx

        respx.post(f"{DUAR}/authz/resolve").mock(side_effect=httpx.ConnectError("boom"))
        client = TestClient(_make_app())
        resp = client.post("/api/duar/authz/resolve", json={"idp_token": "x", "provider": "google"})
        assert resp.status_code == 502


class TestDirectoryProxy:
    @respx.mock
    def test_members_forwards_tokens_and_query_but_not_service_key(self):
        ws = uuid.uuid4()
        route = respx.get(f"{DUAR}/workspaces/{ws}/members").mock(return_value=Response(200, json=[]))
        client = TestClient(_make_app())
        resp = client.get(
            f"/api/duar/workspaces/{ws}/members?q=jan&limit=10",
            headers={"Authorization": "Bearer idp-token", "X-Authz-Token": "authz-token"},
        )
        assert resp.status_code == 200
        sent = route.calls.last.request
        assert sent.url.params["q"] == "jan"
        assert sent.url.params["limit"] == "10"
        assert sent.headers["authorization"] == "Bearer idp-token"
        assert sent.headers["x-authz-token"] == "authz-token"
        # The key must NOT ride along: Duar's flexible auth ignores
        # X-Authz-Token when a valid service key is present, and would try to
        # decode the IdP bearer as a Duar token -> 401.
        assert "x-service-key" not in sent.headers

    @respx.mock
    def test_groups_group_members_and_profile_routes(self):
        ws, group = uuid.uuid4(), uuid.uuid4()
        respx.get(f"{DUAR}/workspaces/{ws}/groups").mock(return_value=Response(200, json=[]))
        respx.get(f"{DUAR}/workspaces/{ws}/groups/{group}/members").mock(return_value=Response(200, json=[]))
        respx.get(f"{DUAR}/users/me").mock(return_value=Response(200, json={"email": "a@b.c"}))
        client = TestClient(_make_app())
        assert client.get(f"/api/duar/workspaces/{ws}/groups").status_code == 200
        assert client.get(f"/api/duar/workspaces/{ws}/groups/{group}/members").status_code == 200
        assert client.get("/api/duar/users/me").json() == {"email": "a@b.c"}


class TestAllowlist:
    @respx.mock
    def test_non_allowlisted_paths_are_not_proxied(self):
        client = TestClient(_make_app())
        for method, path in [
            ("post", "/api/duar/permissions/register"),
            ("get", "/api/duar/admin/users"),
            ("delete", "/api/duar/users/me"),
            ("get", "/api/duar/authz/resolve"),
        ]:
            resp = getattr(client, method)(path)
            assert resp.status_code in (404, 405), path
        assert not respx.calls  # nothing reached the upstream


class TestDuarIntegration:
    def test_duar_proxy_router_uses_configured_url_and_key(self):
        from duar_auth import Duar

        duar = Duar(
            base_url=DUAR,
            service_name="my-app",
            service_key="sk_test",
            mode="authz",
            idp_jwks_url="https://example.com/jwks",
            idp_audience="client-id",
        )
        router = duar.proxy_router()
        with respx.mock:
            route = respx.post(f"{DUAR}/authz/resolve").mock(return_value=Response(200, json={}))
            app = FastAPI()
            app.include_router(router, prefix="/api/duar")
            TestClient(app).post(
                "/api/duar/authz/resolve",
                json={"idp_token": "x", "provider": "google"},
            )
            assert route.calls.last.request.headers["x-service-key"] == "sk_test"
