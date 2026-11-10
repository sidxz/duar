"""Rate limits bite at their configured threshold, and bad input is the
caller's error (4xx), never the server's."""

from conftest import BASE, bearer, service_headers

NOWHERE = "00000000-0000-4000-8000-000000000000"


def test_the_sensitive_admin_tier_stops_the_sixth_call_in_a_minute(admin):
    # Purging a service's permissions is on the 5/minute tier, at its production value.
    responses = [admin.request("DELETE", "/admin/permissions/service/no-such-service") for _ in range(6)]
    assert [r.status_code for r in responses[:5]] == [200] * 5, [r.text for r in responses[:5]]
    limited = responses[5]
    assert limited.status_code == 429
    assert limited.json() == {"detail": "Too many requests"}
    assert limited.headers["retry-after"] == "60"
    # The limit is that route's, for that admin: the rest of the panel still answers.
    assert admin.request("GET", "/admin/users").status_code == 200


def test_malformed_input_is_a_client_error(api, admin, world, alice):
    cases = [
        api.post(f"{BASE}/auth/token", json={"code": "x"}),  # missing fields
        api.post(f"{BASE}/auth/token", content=b"{not json", headers={"Content-Type": "application/json"}),
        api.post(f"{BASE}/auth/refresh", json={"refresh_token": "not-a-token"}),
        api.get(f"{BASE}/workspaces/not-a-uuid", headers=bearer(alice.access_token)),
        api.post(f"{BASE}/permissions/register", json={"resource_id": 1}, headers=service_headers(world.notes_key)),
        api.put(f"{BASE}/auth/token"),  # wrong method
        admin.request("GET", f"/admin/workspaces/{NOWHERE}"),
        admin.request("GET", f"/admin/users/{NOWHERE}"),
        admin.request("POST", "/admin/workspaces", json={"name": "No slug"}),
        admin.request("POST", "/admin/client-apps", json={"name": "Bad URI", "redirect_uris": ["javascript:alert(1)"]}),
    ]
    assert [r.status_code for r in cases] == [422, 422, 401, 422, 422, 405, 404, 404, 422, 422]


def test_a_duplicate_is_refused_not_a_crash(admin):
    taken = admin.get("/admin/workspaces", params={"page_size": 1})["items"][0]["slug"]
    assert admin.request("POST", "/admin/workspaces", json={"name": "Again", "slug": taken}).status_code == 400


def test_an_oversized_body_is_refused(api):
    eleven_megabytes = b"x" * (11 * 1024 * 1024)
    assert api.post(f"{BASE}/auth/token", content=eleven_megabytes).status_code == 413


def test_a_user_only_sees_their_own_workspace(api, world, alice):
    own = api.get(f"{BASE}/workspaces/{world.workspace_id}/members", headers=bearer(alice.access_token))
    assert own.status_code == 200 and "bob@duar.test" in {m["email"] for m in own.json()}
    other = api.get(f"{BASE}/workspaces/{world.other_workspace_id}/members", headers=bearer(alice.access_token))
    assert other.status_code in (403, 404)
