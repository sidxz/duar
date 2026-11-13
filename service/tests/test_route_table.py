"""The service's HTTP surface, pinned.

``route_table.txt`` lists every route of the combined app with the credential
its dependencies demand and the rate limit declared on it. A new endpoint, a
dropped auth dependency or a changed limit shows up as a one-line diff in that
file and fails here until the file is regenerated on purpose:

    UPDATE_ROUTE_TABLE=1 uv run pytest tests/test_route_table.py

The rules below hold whatever the file says: a route may only go without an
auth dependency if it is named in ``NO_AUTH_DEPENDENCY``, and the admin and
internal surfaces may not carry a weaker credential than they do today.

The limit column is what is declared at default settings; "default" means no
decorator, so the route falls to the per-IP default and aggregate tiers. Those,
and the count of failed authentication on the other routes, are applied by the
``address_limits`` dependency, which a rule below keeps first on every route.
"""

import difflib
import os
import re
import uuid
from pathlib import Path

from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient

import src.middleware.rate_limit as rate_limit
from src.config import settings
from src.database import get_db

from src.api.dependencies import (
    get_current_user,
    get_current_user_flexible,
    get_user_for_service_call,
    require_admin,
    require_service_context,
    require_service_key,
)
from src.main import create_app
from src.middleware.rate_limit import address_limits, limiter
from src.services import service_app_service

TABLE = Path(__file__).with_name("route_table.txt")

# Strongest first: a route is labelled with the first of these it depends on.
_AUTH_LABELS = [
    (require_admin, "admin"),
    (get_user_for_service_call, "service-key+user"),
    (require_service_key, "service-key"),
    (require_service_context, "service-key-or-origin"),
    (get_current_user_flexible, "user-or-service-key+user"),
    (get_current_user, "user"),
]

# Routes with no auth dependency. Each either takes no credential by design or
# checks one inside the handler.
NO_AUTH_DEPENDENCY = {
    # Liveness, public keys, static assets, the list of sign-in buttons.
    "GET /health",
    "GET /.well-known/jwks.json",
    "MOUNT /static/fonts",
    "GET /auth/providers",
    # Sign-in round trips: these are how a caller gets a credential.
    "GET /auth/login/{provider}",
    "GET /auth/callback/{provider}",
    "GET /auth/admin/login/{provider}",
    "GET /auth/admin/callback/{provider}",
    "GET /authz/idp/{provider}/login",
    "GET /authz/idp/{provider}/callback",
    # Authenticated in the handler by a one-time code + PKCE verifier, or by
    # the refresh token in the body.
    "POST /auth/workspaces",
    "POST /auth/token",
    "POST /auth/refresh",
    # Hosted onboarding pages: 404 unless SELF_SERVE_ENABLED, then the handler
    # checks the signed session cookie and a CSRF token on every POST.
    "GET /onboard",
    "GET /onboard/login",
    "GET /onboard/login/{provider}",
    "GET /onboard/callback/{provider}",
    "GET /onboard/home",
    "GET /onboard/confirm",
    "GET /onboard/done",
    "GET /onboard/invites",
    "POST /onboard/logout",
    "POST /onboard/join",
    "POST /onboard/create",
    "POST /onboard/invites",
    "POST /onboard/invites/{invitation_id}/revoke",
    "POST /onboard/members/{user_id}/role",
    "POST /onboard/members/{user_id}/remove",
    "POST /onboard/workspace/rename",
    "POST /onboard/leave",
}


def _dependency_calls(dependant) -> set:
    calls = set()
    for dep in dependant.dependencies:
        calls.add(dep.call)
        calls |= _dependency_calls(dep)
    return calls


def _auth(route) -> str:
    dependant = getattr(route, "dependant", None)
    calls = _dependency_calls(dependant) if dependant else set()
    return next((label for dep, label in _AUTH_LABELS if dep in calls), "none")


def _limit(endpoint) -> str:
    key = f"{endpoint.__module__}.{endpoint.__name__}"
    if key in limiter._exempt_routes:
        return "exempt"
    declared = limiter._route_limits.get(key, [])
    if not declared:
        return "default"
    return ", ".join(f"{d.limit} by {d.key_func.__name__}" for d in declared)


def route_rows(tier: str = "all") -> list[tuple[str, str, str]]:
    """(``"METHOD /path"``, auth label, declared limit) for every route."""
    rows = []
    for route in iter_route_contexts(create_app(tier).routes):
        endpoint = route.endpoint
        if endpoint is None:  # a mount (static files)
            rows.append((f"MOUNT {route.path}", "none", "default"))
            continue
        if endpoint.__module__.startswith("fastapi."):
            continue  # /docs, /redoc, /openapi.json: only present when DEBUG=true
        for method in sorted(route.methods - {"HEAD"}):
            rows.append((f"{method} {route.path}", _auth(route), _limit(endpoint)))
    return rows


def _render(rows) -> str:
    return "".join(
        f"{name}  auth={auth}  limit={limit}\n" for name, auth, limit in rows
    )


def test_route_table_matches_the_committed_file():
    actual = _render(route_rows())
    if os.environ.get("UPDATE_ROUTE_TABLE"):
        TABLE.write_text(actual)
    expected = TABLE.read_text()
    diff = "".join(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            "route_table.txt (committed)",
            "the app",
            n=0,
        )
    )
    assert actual == expected, (
        "The HTTP surface changed. If every line below is intended, regenerate with "
        "UPDATE_ROUTE_TABLE=1 uv run pytest tests/test_route_table.py\n" + diff
    )


def test_the_table_covers_the_whole_app():
    # Guards the walk itself: if FastAPI changes how included routers are
    # stored, an empty or partial table must not pass as "nothing changed".
    names = {name for name, _, _ in route_rows()}
    assert len(names) > 100
    for expected in (
        "GET /health",
        "POST /admin/workspaces",
        "POST /auth/token",
        "POST /permissions/check",
        "GET /workspaces/{workspace_id}/groups/{group_id}/members",
    ):
        assert expected in names


def test_every_route_applies_the_address_limits_before_its_own_dependencies():
    # First in line means ahead of authentication, on each listener.
    for tier, at_least in (("all", 100), ("public", 80), ("internal", 10)):
        dependants = [
            (route.path, route.dependant)
            for route in iter_route_contexts(create_app(tier).routes)
            if getattr(route, "dependant", None) is not None
        ]
        assert len(dependants) > at_least, tier
        assert [
            path
            for path, dependant in dependants
            if dependant.dependencies[0].call is not address_limits
        ] == [], tier


def _counting_client(monkeypatch):
    """The combined app without a database, and the list of buckets that
    failed authentication gets counted in."""
    counted = []

    def record(active, call, lim, *bucket):
        counted.append(bucket)
        return True

    async def no_db():
        yield None

    monkeypatch.setattr(rate_limit, "_storage_call", record)
    monkeypatch.setattr(limiter, "enabled", True)
    app = create_app("all")
    app.dependency_overrides[get_db] = no_db
    return TestClient(app, base_url=settings.base_url), counted


def test_an_anonymous_request_to_a_route_limited_per_caller_is_a_counted_401(
    monkeypatch,
):
    # Failed authentication is counted where a 401 passes through
    # address_limits. A guard that answered 403, or returned its own response,
    # would go uncounted on that route.
    client, counted = _counting_client(monkeypatch)
    per_caller = [
        name
        for name, _, limit in route_rows()
        if limit not in ("default", "exempt") and not limit.endswith("get_client_ip")
    ]
    assert len(per_caller) > 25
    for name in per_caller:
        method, path = name.split()
        counted.clear()
        url = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path)
        response = client.request(method, url, json={})
        assert (response.status_code, len(counted)) == (401, 1), name


def test_a_wrong_service_key_is_a_counted_401_whatever_origin_comes_with_it(
    monkeypatch,
):
    async def unknown_key(key, db):
        return None

    async def registered_origin(origin, db):
        return "notes", uuid.uuid4()

    client, counted = _counting_client(monkeypatch)
    monkeypatch.setattr(service_app_service, "validate_key", unknown_key)
    monkeypatch.setattr(service_app_service, "validate_origin", registered_origin)
    credentials = {"X-Service-Key": "sk_guess", "Origin": "https://notes.example"}
    response = client.post("/authz/resolve", json={}, headers=credentials)
    assert (response.status_code, len(counted)) == (401, 1)


def test_a_401_behind_a_valid_service_key_is_not_counted(monkeypatch):
    # A backend relays its users' tokens from one address. Both places a key
    # is validated: the service-key routes and the ones that take one optionally.
    async def valid_key(key, db):
        return "notes", uuid.uuid4(), None

    client, counted = _counting_client(monkeypatch)
    monkeypatch.setattr(service_app_service, "validate_key", valid_key)
    for method, path in (
        ("POST", "/permissions/check"),
        ("GET", f"/workspaces/{uuid.uuid4()}/members"),
    ):
        response = client.request(method, path, headers={"X-Service-Key": "sk_real"})
        assert (response.status_code, counted) == (401, []), path


def test_a_route_limited_per_address_takes_no_credential_in_a_dependency():
    # Its limit is counted in the endpoint wrapper, which a rejection by a
    # dependency never reaches, and failed authentication is only counted on
    # the routes limited per caller.
    rows = [
        (name, auth)
        for name, auth, limit in route_rows()
        if limit not in ("default", "exempt")
        and all(part.endswith(" by get_client_ip") for part in limit.split(", "))
    ]
    assert len(rows) > 5
    assert [row for row in rows if row[1] != "none"] == []


def test_only_listed_routes_go_without_an_auth_dependency():
    unauthenticated = {name for name, auth, _ in route_rows() if auth == "none"}
    assert unauthenticated - NO_AUTH_DEPENDENCY == set(), (
        "new route(s) with no auth dependency"
    )
    assert NO_AUTH_DEPENDENCY - unauthenticated == set(), "stale allowlist entries"


def test_every_admin_route_requires_the_admin_session():
    # require_admin is also where the X-Requested-With (CSRF) check lives.
    rows = [
        (name, auth)
        for name, auth, _ in route_rows()
        if name.split()[1].startswith("/admin")
    ]
    assert len(rows) > 50
    assert [row for row in rows if row[1] != "admin"] == []


def test_every_internal_listener_route_requires_a_service_credential():
    rows = [
        (name, auth)
        for name, auth, _ in route_rows("internal")
        if name != "GET /health"
    ]
    assert len(rows) > 10
    allowed = {"service-key", "service-key+user", "service-key-or-origin"}
    assert [row for row in rows if row[1] not in allowed] == []


def test_every_route_a_backend_calls_is_limited_per_caller():
    # A backend calls these from one address on behalf of all its users, so a
    # per-address tier would throttle the whole app at once. That covers the
    # routes that take a service key and the ones that take it optionally.
    backend = {
        "service-key",
        "service-key+user",
        "service-key-or-origin",
        "user-or-service-key+user",
    }
    per_caller = (" by service_user_key", " by service_or_ip_key", " by user_or_ip_key")
    rows = [(name, limit) for name, auth, limit in route_rows() if auth in backend]
    assert len(rows) > 15
    assert [row for row in rows if not row[1].endswith(per_caller)] == []


def test_a_limit_shared_by_a_whole_service_is_on_a_service_sized_tier():
    # Keyed by calling service means one bucket for all of that app's users.
    # Only token minting (60/minute, cached by the SDKs) may sit below the
    # service tier there.
    minting = {"POST /authz/resolve", "GET /realm/whoami", "POST /realm/m2m-token"}
    rows = [
        (name, limit)
        for name, _, limit in route_rows()
        if limit.endswith(" by service_or_ip_key") and name not in minting
    ]
    assert len(rows) > 5
    assert [row for row in rows if not row[1].startswith("3000 per 1 minute")] == []
