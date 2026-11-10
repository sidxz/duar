"""Signing in, staying signed in, and being signed out (proxy mode and admin)."""

import secrets

from conftest import ADMIN, APP_CALLBACK, BASE, Browser, authorization_code, bearer, login_url, pkce, user_session


def token_request(api, world, code, verifier):
    body = {"code": code, "workspace_id": world.workspace_id, "code_verifier": verifier}
    return api.post(f"{BASE}/auth/token", json=body)


# ── Admin session ────────────────────────────────────────────────────────────


def test_admin_session_cookie_is_locked_down(admin):
    assert admin.get("/auth/admin/me")["email"] == "admin@duar.test"
    cookie = next(c for c in admin.browser.http.cookies.jar if c.name == "admin_token")
    assert cookie.secure
    assert cookie.has_nonstandard_attr("HttpOnly")
    assert cookie.get_nonstandard_attr("SameSite").lower() == "strict"


def test_admin_api_refuses_a_write_without_the_csrf_header(admin):
    body = {"name": "CSRF probe", "slug": f"csrf-{secrets.token_hex(3)}"}
    # The session cookie alone, as a cross-site form post would carry it.
    forged = admin.browser.http.post(f"{ADMIN}/api/admin/workspaces", json=body)
    assert forged.status_code == 403
    assert admin.post("/admin/workspaces", body)["slug"] == body["slug"]


def test_admin_api_refuses_anonymous_callers(api):
    assert api.get(f"{BASE}/admin/users").status_code == 401
    assert api.get(f"{ADMIN}/api/admin/users").status_code == 401


def test_a_non_admin_cannot_open_an_admin_session(world):
    browser = Browser()
    landed = browser.sign_in(f"{BASE}/auth/admin/login/dex", "bob@duar.test", until=ADMIN)
    assert landed == f"{ADMIN}/login?error=not_admin"
    assert "admin_token" not in browser.http.cookies
    assert browser.http.get(f"{BASE}/auth/admin/me").status_code == 401


def test_admin_logout_kills_the_session_cookie(world):
    browser = Browser()
    browser.sign_in(f"{BASE}/auth/admin/login/dex", "admin@duar.test", until=ADMIN)
    assert browser.http.get(f"{BASE}/auth/admin/me").status_code == 200
    token = browser.http.cookies["admin_token"]
    out = browser.http.post(f"{BASE}/auth/admin/logout", headers={"X-Requested-With": "XMLHttpRequest"})
    assert out.status_code == 200
    # The same token replayed after logout, as a copied cookie would be.
    replay = Browser().http.get(f"{BASE}/auth/admin/me", headers={"Cookie": f"admin_token={token}"})
    assert replay.status_code == 401


# ── User sign-in (authorization code + PKCE) ─────────────────────────────────


def test_sign_in_returns_the_apps_state_and_lists_the_users_workspaces(api, world):
    _, code, verifier, state = authorization_code(world, "alice@duar.test", state="opaque-123")
    assert state == "opaque-123"
    listed = api.post(f"{BASE}/auth/workspaces", json={"code": code, "code_verifier": verifier})
    assert listed.status_code == 200
    assert {w["id"]: w["role"] for w in listed.json()}[world.workspace_id] == "admin"
    # Listing does not spend the code.
    assert token_request(api, world, code, verifier).status_code == 200


def test_a_code_is_useless_without_its_pkce_verifier(api, world):
    _, code, verifier, _ = authorization_code(world, "alice@duar.test")
    other_verifier, _ = pkce()
    stolen = api.post(f"{BASE}/auth/workspaces", json={"code": code, "code_verifier": other_verifier})
    assert stolen.status_code == 400
    assert token_request(api, world, code, other_verifier).status_code == 400
    # ...and that failed attempt spends the code, even for its rightful owner.
    assert token_request(api, world, code, verifier).status_code == 400


def test_a_code_works_once(api, world):
    _, code, verifier, _ = authorization_code(world, "alice@duar.test")
    assert token_request(api, world, code, verifier).status_code == 200
    assert token_request(api, world, code, verifier).status_code == 400


def test_a_member_cannot_get_tokens_for_a_workspace_they_are_not_in(api, world):
    _, code, verifier, _ = authorization_code(world, "bob@duar.test")
    body = {"code": code, "workspace_id": world.other_workspace_id, "code_verifier": verifier}
    assert api.post(f"{BASE}/auth/token", json=body).status_code == 403


def test_sign_in_is_refused_for_an_unregistered_redirect_uri(world):
    _, challenge = pkce()
    browser = Browser()
    ok = browser.follow(login_url(world.client_app_id, challenge))
    assert "/dex/auth/local" in str(ok.url), "control: a registered URI reaches the provider"
    refused = Browser().follow(login_url(world.client_app_id, challenge, redirect_uri="https://evil.example/cb"))
    assert refused.status_code == 400 and BASE in str(refused.url)
    assert "evil.example" not in refused.headers.get("location", "")


def test_sign_in_is_refused_for_an_unknown_client_app(world):
    _, challenge = pkce()
    refused = Browser().follow(login_url("00000000-0000-4000-8000-000000000000", challenge))
    assert refused.status_code == 400 and BASE in str(refused.url)


def test_sign_in_requires_s256_pkce(world):
    _, challenge = pkce()
    plain = login_url(world.client_app_id, challenge).replace(
        "code_challenge_method=S256", "code_challenge_method=plain"
    )
    assert Browser().follow(plain).status_code == 400


# ── Session lifetime ─────────────────────────────────────────────────────────


def test_refresh_rotates_and_a_replayed_refresh_token_revokes_the_family(api, world):
    first = user_session(world, "bob@duar.test")
    second = api.post(f"{BASE}/auth/refresh", json={"refresh_token": first.refresh_token})
    assert second.status_code == 200
    second = second.json()
    assert second["refresh_token"] != first.refresh_token
    assert api.get(f"{BASE}/users/me", headers=bearer(second["access_token"])).status_code == 200
    # The spent token again: a stolen copy being replayed.
    assert api.post(f"{BASE}/auth/refresh", json={"refresh_token": first.refresh_token}).status_code == 401
    # Which takes the legitimate holder's token down with it.
    assert api.post(f"{BASE}/auth/refresh", json={"refresh_token": second["refresh_token"]}).status_code == 401


def test_logout_revokes_the_access_token_and_the_refresh_token(api, world):
    session = user_session(world, "bob@duar.test")
    assert api.get(f"{BASE}/users/me", headers=bearer(session.access_token)).status_code == 200
    assert api.post(f"{BASE}/auth/logout", headers=bearer(session.access_token)).status_code == 200
    assert api.get(f"{BASE}/users/me", headers=bearer(session.access_token)).status_code == 401
    assert api.post(f"{BASE}/auth/refresh", json={"refresh_token": session.refresh_token}).status_code == 401


def test_a_token_of_the_wrong_kind_is_not_an_access_token(api, alice):
    assert api.get(f"{BASE}/users/me", headers=bearer(alice.refresh_token)).status_code == 401
    assert api.get(f"{BASE}/users/me", headers=bearer(alice.access_token)).status_code == 200


def test_deactivating_a_user_cuts_off_their_tokens_and_their_sign_in(api, world, admin):
    eve = user_session(world, "eve@duar.test")
    assert api.get(f"{BASE}/users/me", headers=bearer(eve.access_token)).status_code == 200
    user_id = world.users["eve@duar.test"]
    admin.call("PATCH", f"/admin/users/{user_id}", 200, json={"is_active": False})
    assert api.get(f"{BASE}/users/me", headers=bearer(eve.access_token)).status_code == 401
    assert api.post(f"{BASE}/auth/refresh", json={"refresh_token": eve.refresh_token}).status_code == 401
    _, challenge = pkce()
    again = Browser().sign_in(login_url(world.client_app_id, challenge), "eve@duar.test", until=APP_CALLBACK)
    assert not isinstance(again, str) and again.status_code == 403

    # Reactivating lets her sign in again; the old tokens stay dead.
    admin.call("PATCH", f"/admin/users/{user_id}", 200, json={"is_active": True})
    back = user_session(world, "eve@duar.test")
    assert api.get(f"{BASE}/users/me", headers=bearer(back.access_token)).status_code == 200
    assert api.post(f"{BASE}/auth/refresh", json={"refresh_token": eve.refresh_token}).status_code == 401
