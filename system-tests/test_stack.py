"""The stack as deployed: what answers where, and what must not."""

import secrets

import httpx
import jwt

from conftest import ADMIN, BASE, INTERNAL, service_headers


def test_service_answers_over_tls_with_hardening_headers(api):
    response = api.get(f"{BASE}/health")
    assert response.status_code == 200 and response.json() == {"status": "ok"}
    assert "max-age" in response.headers["strict-transport-security"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


def test_api_docs_are_off_outside_debug(api):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert api.get(f"{BASE}{path}").status_code == 404, path


def test_signing_keys_are_published(api):
    keys = api.get(f"{BASE}/.well-known/jwks.json").json()["keys"]
    assert keys and all(k["kty"] == "RSA" and k["alg"] == "RS256" and k["kid"] for k in keys)
    assert not any("d" in k for k in keys), "a private exponent in the published key set"


def test_only_the_configured_provider_is_offered(api):
    assert api.get(f"{BASE}/auth/providers").json() == {"providers": ["dex"]}


def test_a_request_for_another_host_is_refused():
    # Straight at the service's own port: the Host allowlist is its job, not the proxy's.
    direct = "http://127.0.0.1:19003/auth/providers"
    assert httpx.get(direct, headers={"Host": "localhost"}).status_code == 200
    assert httpx.get(direct, headers={"Host": "evil.example"}).status_code == 400


def test_admin_panel_serves_the_app_and_proxies_the_api(api):
    index = api.get(f"{ADMIN}/")
    assert index.status_code == 200 and '<div id="root">' in index.text
    assert index.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in index.headers["content-security-policy"]
    # A deep link is the app's to route, not a 404.
    assert '<div id="root">' in api.get(f"{ADMIN}/workspaces/anything").text
    assert api.get(f"{ADMIN}/api/auth/providers").json() == {"providers": ["dex"]}


def test_a_request_through_the_admin_panel_is_attributed_to_the_same_client(api, admin):
    # Rate limits and the audit log key on the client address the service works
    # out from the proxy chain. The panel's own proxy is one more hop on that
    # chain, and must not change the answer.
    probe = secrets.token_hex(4)
    for base, path in ((BASE, "direct"), (f"{ADMIN}/api", "panel")):
        refused = api.get(f"{base}/auth/admin/login/{path}-{probe}")
        assert refused.status_code == 400  # no such provider; the refusal is audited
    rows = admin.get("/admin/activity", params={"action": "admin_login_failed", "page_size": 20})["items"]
    seen = {
        row["detail"]["provider"].split("-")[0]: row["detail"]["ip"]
        for row in rows
        if row["detail"]["provider"].endswith(probe)
    }
    assert set(seen) == {"direct", "panel"}
    assert seen["panel"] == seen["direct"]


def test_database_and_redis_report_healthy(admin):
    health = admin.get("/admin/system/health")
    assert health["status"] == "healthy", health
    assert {name: check["status"] for name, check in health["checks"].items()} == {"database": "ok", "redis": "ok"}


def test_the_stack_runs_with_production_settings(admin):
    # Guards the run itself: these tests mean little against a debug-mode stack.
    settings = admin.get("/admin/system/settings")
    assert settings["security"]["cookie_secure"] is True
    assert settings["security"]["allowed_hosts"] == ["localhost"]
    limits = {row["endpoint"].split(" ")[0]: row["limit"] for row in settings["rate_limits"]}
    assert (limits["sensitive"], limits["authz"], limits["default"]) == ("5/minute", "60/minute", "120/minute")


def test_internal_listener_carries_the_service_key_surface_only(api, world):
    whoami = api.get(f"{INTERNAL}/realm/whoami", headers=service_headers(world.notes_key))
    assert whoami.status_code == 200 and whoami.json()["service_name"] == world.notes
    assert api.get(f"{INTERNAL}/realm/whoami").status_code == 401
    assert api.get(f"{INTERNAL}/realm/whoami", headers=service_headers("not-a-key")).status_code == 401
    for path in ("/auth/providers", "/admin/users", "/users/me", "/onboard"):
        assert api.get(f"{INTERNAL}{path}").status_code == 404, path


def test_access_token_verifies_against_the_published_keys(api, world, alice):
    keys = {k["kid"]: k for k in api.get(f"{BASE}/.well-known/jwks.json").json()["keys"]}
    kid = jwt.get_unverified_header(alice.access_token)["kid"]
    claims = jwt.decode(
        alice.access_token,
        jwt.PyJWK(keys[kid]).key,
        algorithms=["RS256"],
        audience="duar:access",
        issuer=BASE,
    )
    assert claims["email"] == "alice@duar.test"
    assert (claims["wid"], claims["wrole"], claims["type"]) == (world.workspace_id, "admin", "access")
