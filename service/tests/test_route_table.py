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
decorator, so the route falls to the per-IP default and aggregate tiers.
"""

import difflib
import os
from pathlib import Path

from fastapi.routing import iter_route_contexts

from src.api.dependencies import (
    get_current_user,
    get_current_user_flexible,
    get_user_for_service_call,
    require_admin,
    require_service_context,
    require_service_key,
)
from src.main import create_app
from src.middleware.rate_limit import limiter

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
