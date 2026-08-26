"""create_app(tier) mounts the right routers + middleware per listener tier.

Apps are inspected at CONSTRUCTION time — building a FastAPI app does not run its
lifespan, so no DB/Redis is touched. 'all' (the default) must carry every route so
dev/make-start/tests are unchanged; 'public' drops the service-key surface; 'internal'
drops the browser surface AND the Session/CORS middleware.
"""

import pytest
from starlette.middleware.sessions import SessionMiddleware

from src.main import INTERNAL_ROUTERS, PUBLIC_ROUTERS, _resolve_tier, create_app
from src.middleware.cors import DynamicCORSMiddleware


def _paths(app) -> set[str]:
    # FastAPI 0.138+ stores include_router results as a lazy wrapper (_IncludedRouter)
    # that exposes the real APIRoutes via .original_router. Gate on the attribute we
    # read (hasattr), not the private class name — a rename would otherwise silently
    # drop every included path and make the positive assertions vacuously pass.
    result: set[str] = set()
    for r in app.routes:
        if hasattr(r, "original_router"):
            for sub in r.original_router.routes:
                result.add(getattr(sub, "path", ""))
        else:
            result.add(getattr(r, "path", ""))
    return result


def _has_prefix(app, prefix: str) -> bool:
    return any(p == prefix or p.startswith(prefix + "/") for p in _paths(app))


def test_public_tier_has_browser_routes_not_service_key_surface():
    app = create_app("public")
    assert _has_prefix(app, "/auth")  # auth proxy — public
    assert _has_prefix(app, "/admin")  # admin — public
    assert _has_prefix(app, "/users")  # public
    assert _has_prefix(app, "/onboard")  # self-serve onboarding — public
    # Service-key surface must be ABSENT from the public listener:
    assert "/authz/resolve" not in _paths(app)  # authz SERVICE surface — internal only
    assert not _has_prefix(app, "/permissions")
    assert not _has_prefix(app, "/realm")
    assert not _has_prefix(app, "/roles")
    # ...but the browser-facing GitHub proxy-login IS public (it needs Session):
    assert any(p.startswith("/authz/idp") for p in _paths(app))


def test_internal_tier_has_service_key_surface_not_browser_routes():
    app = create_app("internal")
    assert "/authz/resolve" in _paths(app)  # authz SERVICE surface — internal
    assert _has_prefix(app, "/permissions")
    assert _has_prefix(app, "/realm")  # Plan 2 router, mounted here
    assert _has_prefix(app, "/roles")
    # Browser surface must be ABSENT from the internal listener:
    assert not _has_prefix(app, "/auth")  # /auth proxy — public
    assert not any(
        p.startswith("/authz/idp") for p in _paths(app)
    )  # idp proxy — public
    assert not _has_prefix(app, "/admin")
    assert not _has_prefix(app, "/users")
    assert not _has_prefix(app, "/workspaces")
    assert not _has_prefix(app, "/onboard")


def test_all_tier_is_todays_app_superset():
    app = create_app("all")
    for prefix in (
        "/auth",
        "/admin",
        "/users",
        "/workspaces",
        "/authz",
        "/permissions",
        "/realm",
        "/roles",
        "/onboard",
    ):
        assert _has_prefix(app, prefix), prefix
    assert "/authz/resolve" in _paths(app)
    assert any(p.startswith("/authz/idp") for p in _paths(app))


def test_health_on_every_tier():
    for tier in ("public", "internal", "all"):
        assert "/health" in _paths(create_app(tier))


def test_jwks_public_and_all_not_internal():
    assert "/.well-known/jwks.json" in _paths(create_app("public"))
    assert "/.well-known/jwks.json" in _paths(create_app("all"))
    assert "/.well-known/jwks.json" not in _paths(create_app("internal"))


def test_internal_tier_drops_session_and_cors_middleware():
    internal = {m.cls for m in create_app("internal").user_middleware}
    assert SessionMiddleware not in internal
    assert DynamicCORSMiddleware not in internal
    public = {m.cls for m in create_app("public").user_middleware}
    assert SessionMiddleware in public
    assert DynamicCORSMiddleware in public


def test_realm_router_is_internal_only():
    from src.api.realm_routes import router as realm_router

    assert realm_router in INTERNAL_ROUTERS
    assert realm_router not in PUBLIC_ROUTERS


def test_resolve_tier_default_is_all(monkeypatch):
    monkeypatch.delenv("TIER", raising=False)
    assert _resolve_tier() == "all"


def test_resolve_tier_rejects_unknown(monkeypatch):
    monkeypatch.setenv("TIER", "bogus")
    with pytest.raises(RuntimeError):
        _resolve_tier()


def test_authz_idp_router_is_public_resolve_is_internal():
    """The browser GitHub-proxy router (/authz/idp/*) is public (needs Session); the
    service-key /authz/resolve stays internal — so the network split doesn't strand
    session-using browser routes on the no-Session internal listener."""
    from src.api.authz_routes import idp_router as authz_idp_router
    from src.api.authz_routes import router as authz_router

    assert authz_idp_router in PUBLIC_ROUTERS
    assert authz_idp_router not in INTERNAL_ROUTERS
    assert authz_router in INTERNAL_ROUTERS
    assert authz_router not in PUBLIC_ROUTERS


def test_paths_helper_actually_enumerates_included_routes():
    # Guard against a silent regression: if route introspection breaks, _paths()
    # would return an almost-empty set and the tier assertions above would go
    # vacuous. A healthy 'all' app exposes well over a dozen paths.
    assert len(_paths(create_app("all"))) > 10
