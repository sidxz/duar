"""Hosted /onboard pages — part A (entry, login, callback, home, done, logout).

Real SQLite DB behind get_db; the IdP round-trip is faked by patching
``onboard_routes.oauth`` and the session cookie is forged with the same signer
starlette uses (tests.self_serve_fixtures.session_cookie).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

from src.api import onboard_routes
from src.api.onboard_routes import router as onboard_router
from src.config import settings
from src.database import get_db
from src.middleware.rate_limit import limiter
from src.models.organization import Organization
from src.models.user import User
from src.models.workspace import Workspace, WorkspaceMembership
from src.services import invitation_service, signal_service
from tests.self_serve_fixtures import make_engine, session_cookie

SECRET = "test-secret"
PUBLIC_ORG_ID = uuid.UUID("00000000-0000-0000-0000-00000000000a")
NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    original = limiter.enabled
    limiter.enabled = False
    monkeypatch.setattr(settings, "self_serve_enabled", True)
    monkeypatch.setattr(settings, "base_url", "http://testserver")
    monkeypatch.setattr(settings, "session_secret_key", SECRET)
    yield
    limiter.enabled = original


@pytest_asyncio.fixture
async def db():
    engine = await make_engine()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        session.add(
            Organization(
                id=PUBLIC_ORG_ID,
                slug="public",
                name="Public",
                is_public=True,
                enabled=True,
            )
        )
        await session.commit()
        yield session
    await engine.dispose()


@pytest.fixture
def client(db):
    app = FastAPI()
    app.add_middleware(
        SessionMiddleware, secret_key=SECRET, same_site="lax", max_age=600
    )
    app.include_router(onboard_router)

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    return TestClient(app, follow_redirects=False)


async def _user(db, email="u@example.com", active=True) -> User:
    u = User(email=email, name="U", organization_id=PUBLIC_ORG_ID, is_active=active)
    db.add(u)
    await db.commit()
    return u


def _login(client, user_id, extra=None):
    data = {"onboard_user_id": str(user_id), "onboard_csrf": "tok", **(extra or {})}
    # domain pinned to match what TestClient's cookiejar normalizes server-issued
    # cookies to for the single-label host "testserver" (stdlib http.cookiejar's
    # eff_request_host() appends ".local"). Without this, the manually-injected
    # cookie lands in a separate jar bucket from the one the app's own Set-Cookie
    # responses use, so it never gets updated/cleared (e.g. by logout).
    client.cookies.set(
        "session", session_cookie(data, SECRET), domain="testserver.local"
    )


# ── flag ──────────────────────────────────────────────────────────────


def test_everything_404_when_flag_off(client, monkeypatch):
    monkeypatch.setattr(settings, "self_serve_enabled", False)
    for path in (
        "/onboard",
        "/onboard/home",
        "/onboard/login/google",
        "/onboard/callback/google",
        "/onboard/done",
        "/onboard/login",
        "/onboard/confirm",  # would be a 422 (missing params) if validation ran first
    ):
        assert client.get(path).status_code == 404, path
    assert client.post("/onboard/logout", data={"csrf": "x"}).status_code == 404
    assert client.post("/onboard/join", data={}).status_code == 404
    assert client.post("/onboard/members/not-a-uuid/role", data={}).status_code == 404


# ── entry ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_entry_prg_stores_code_and_validated_return_to(client, db):
    with patch.object(
        onboard_routes,
        "service_app_origin_allowed",
        AsyncMock(return_value=True),
    ):
        r = client.get(
            "/onboard", params={"code": "abc", "return_to": "https://app.example/login"}
        )
    assert r.status_code == 303 and r.headers["location"] == "http://testserver/onboard"
    with patch.object(
        onboard_routes, "get_configured_providers", return_value=["google", "github"]
    ):
        page = client.get("/onboard")
    assert page.status_code == 200 and "/onboard/login/google" in page.text
    # keys survived the PRG (peek at the signed cookie the way the server does)
    from base64 import b64decode
    import json
    from itsdangerous import TimestampSigner

    raw = TimestampSigner(SECRET).unsign(client.cookies["session"])
    sess = json.loads(b64decode(raw))
    assert sess["onboard_code"] == "abc"
    assert sess["onboard_return_to"] == "https://app.example/login"


def test_entry_rejects_off_allowlist_return_to(client):
    with patch.object(
        onboard_routes,
        "service_app_origin_allowed",
        AsyncMock(return_value=False),
    ):
        r = client.get("/onboard", params={"return_to": "https://evil.example/"})
    assert r.status_code == 400 and "Return" in r.text
    assert "session" not in client.cookies


def test_entry_malformed_return_to_is_400_not_500(client):
    # urlparse raises ValueError on an unbalanced '[' — must read as "malformed".
    r = client.get("/onboard", params={"return_to": "http://["})
    assert r.status_code == 400 and "Return" in r.text


@pytest.mark.asyncio
async def test_entry_empty_return_to_treated_as_absent_keeps_code(client, db):
    # return_to="" must not validate-and-reject (which would also drop the
    # accompanying code) — it's treated the same as return_to being absent.
    r = client.get("/onboard", params={"return_to": "", "code": "abc"})
    assert r.status_code == 303 and r.headers["location"] == "http://testserver/onboard"
    from base64 import b64decode
    import json
    from itsdangerous import TimestampSigner

    raw = TimestampSigner(SECRET).unsign(client.cookies["session"])
    sess = json.loads(b64decode(raw))
    assert sess["onboard_code"] == "abc"
    assert "onboard_return_to" not in sess


def test_entry_rejects_oversized_return_to(client):
    r = client.get("/onboard", params={"return_to": "https://a.example/" + "x" * 2100})
    assert r.status_code == 400


def test_entry_single_provider_redirects_straight_to_login(client):
    with patch.object(
        onboard_routes, "get_configured_providers", return_value=["google"]
    ):
        r = client.get("/onboard")
    assert r.status_code == 302 and r.headers["location"].endswith(
        "/onboard/login/google"
    )


def test_entry_provider_param_only_when_configured(client):
    with patch.object(
        onboard_routes, "get_configured_providers", return_value=["google", "github"]
    ):
        assert (
            client.get("/onboard", params={"provider": "github"})
            .headers["location"]
            .endswith("/login/github")
        )
        assert client.get("/onboard", params={"provider": "dex"}).status_code == 200


@pytest.mark.asyncio
async def test_entry_signed_in_goes_home_and_keeps_identity(client, db):
    u = await _user(db)
    _login(client, u.id)
    r = client.get("/onboard", params={"code": "zzz"})
    assert r.status_code == 303
    r = client.get("/onboard")
    assert r.status_code == 302 and r.headers["location"].endswith("/onboard/home")


@pytest.mark.asyncio
async def test_entry_ignores_offsite_onboard_next(client, db):
    u = await _user(db)
    _login(client, u.id, {"onboard_next": "https://evil.example/"})
    r = client.get("/onboard")
    assert (
        r.status_code == 302
        and r.headers["location"] == "http://testserver/onboard/home"
    )


# ── login / callback ──────────────────────────────────────────────────


def test_login_unconfigured_provider(client):
    with patch.object(
        onboard_routes, "get_configured_providers", return_value=["google"]
    ):
        r = client.get("/onboard/login/github")
    assert r.status_code == 400 and "Back to sign-in" in r.text


def test_login_redirects_to_idp_with_onboard_callback(client):
    from starlette.responses import RedirectResponse

    fake = SimpleNamespace(
        authorize_redirect=AsyncMock(
            return_value=RedirectResponse("https://idp/auth", status_code=302)
        )
    )
    with (
        patch.object(
            onboard_routes, "get_configured_providers", return_value=["google"]
        ),
        patch.object(onboard_routes.oauth, "create_client", return_value=fake),
    ):
        r = client.get("/onboard/login/google")
    assert r.status_code == 302
    args, kwargs = fake.authorize_redirect.await_args
    assert args[1] == "http://testserver/onboard/callback/google"
    assert kwargs["prompt"] == "select_account"  # never silently re-use the IdP session


def test_login_page_is_the_chooser_even_with_one_provider(client):
    with patch.object(
        onboard_routes, "get_configured_providers", return_value=["google"]
    ):
        r = client.get("/onboard/login")
    assert r.status_code == 200 and "Continue with Google" in r.text


def _fake_oidc_client(userinfo):
    return SimpleNamespace(
        authorize_access_token=AsyncMock(return_value={"userinfo": userinfo})
    )


@pytest.mark.asyncio
async def test_callback_provisions_user_sets_session_and_audits(client, db):
    userinfo = {
        "sub": "g-1",
        "email": "new@example.com",
        "email_verified": True,
        "name": "New",
    }
    with (
        patch.object(
            onboard_routes, "get_configured_providers", return_value=["google"]
        ),
        patch.object(
            onboard_routes.oauth,
            "create_client",
            return_value=_fake_oidc_client(userinfo),
        ),
        patch.object(signal_service, "on_login_success", new_callable=AsyncMock) as sig,
    ):
        r = client.get("/onboard/callback/google")
    assert r.status_code == 302 and r.headers["location"].endswith("/onboard/home")
    sig.assert_awaited_once()
    user = await db.scalar(
        __import__("sqlalchemy").select(User).where(User.email == "new@example.com")
    )
    assert user is not None
    home = client.get("/onboard/home")
    assert home.status_code == 200 and "New" in home.text


@pytest.mark.asyncio
async def test_callback_honors_onboard_next(client, db):
    client.cookies.set(
        "session",
        session_cookie({"onboard_next": "http://testserver/onboard/invites"}, SECRET),
    )
    userinfo = {
        "sub": "g-2",
        "email": "n2@example.com",
        "email_verified": True,
        "name": "N2",
    }
    with (
        patch.object(
            onboard_routes, "get_configured_providers", return_value=["google"]
        ),
        patch.object(
            onboard_routes.oauth,
            "create_client",
            return_value=_fake_oidc_client(userinfo),
        ),
        patch.object(signal_service, "on_login_success", new_callable=AsyncMock),
    ):
        r = client.get("/onboard/callback/google")
    assert r.headers["location"] == "http://testserver/onboard/invites"


@pytest.mark.asyncio
async def test_callback_unverified_email_is_403_with_back_link(client, db):
    userinfo = {"sub": "g-3", "email": "x@example.com", "email_verified": False}
    with (
        patch.object(
            onboard_routes, "get_configured_providers", return_value=["google"]
        ),
        patch.object(
            onboard_routes.oauth,
            "create_client",
            return_value=_fake_oidc_client(userinfo),
        ),
        patch.object(signal_service, "on_login_failure", new_callable=AsyncMock),
    ):
        r = client.get("/onboard/callback/google")
    assert r.status_code == 403 and "Back to sign-in" in r.text


@pytest.mark.asyncio
async def test_callback_entra_requires_xms_edov(client, db, monkeypatch):
    monkeypatch.setattr(settings, "entra_tenant_id", "tid")
    userinfo = {"sub": "e-1", "email": "e@example.com", "tid": "tid", "name": "E"}
    with (
        patch.object(
            onboard_routes, "get_configured_providers", return_value=["entra_id"]
        ),
        patch.object(
            onboard_routes.oauth,
            "create_client",
            return_value=_fake_oidc_client(userinfo),
        ),
        patch.object(signal_service, "on_login_failure", new_callable=AsyncMock),
    ):
        r = client.get("/onboard/callback/entra_id")
    assert r.status_code == 403
    userinfo["xms_edov"] = True
    with (
        patch.object(
            onboard_routes, "get_configured_providers", return_value=["entra_id"]
        ),
        patch.object(
            onboard_routes.oauth,
            "create_client",
            return_value=_fake_oidc_client(userinfo),
        ),
        patch.object(signal_service, "on_login_success", new_callable=AsyncMock),
    ):
        r = client.get("/onboard/callback/entra_id")
    assert r.status_code == 302


@pytest.mark.asyncio
async def test_callback_inactive_user_403(client, db):
    await _user(db, "dead@example.com", active=False)
    userinfo = {
        "sub": "g-4",
        "email": "dead@example.com",
        "email_verified": True,
        "name": "D",
    }
    with (
        patch.object(
            onboard_routes, "get_configured_providers", return_value=["google"]
        ),
        patch.object(
            onboard_routes.oauth,
            "create_client",
            return_value=_fake_oidc_client(userinfo),
        ),
        patch.object(signal_service, "on_login_failure", new_callable=AsyncMock),
    ):
        r = client.get("/onboard/callback/google")
    assert r.status_code == 403


# ── home / done / logout ──────────────────────────────────────────────


def test_home_without_session_redirects(client):
    r = client.get("/onboard/home")
    assert r.status_code == 302 and r.headers["location"].endswith("/onboard")


@pytest.mark.asyncio
async def test_home_deactivated_user_is_logged_out(client, db):
    u = await _user(db, active=False)
    _login(client, u.id)
    r = client.get("/onboard/home")
    assert r.status_code == 302
    # the onboard_* session was actually cleared, not just redirected past
    assert "session=null" in r.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_home_shows_invite_card_and_sections(client, db):
    owner = await _user(db, "o@example.com")
    ws = Workspace(name="Acme", slug="acme", created_by=owner.id)
    db.add(ws)
    await db.flush()
    db.add(WorkspaceMembership(workspace_id=ws.id, user_id=owner.id, role="owner"))
    await db.commit()
    _, code = await invitation_service.create(
        db, workspace_id=ws.id, role="editor", created_by=owner.id, actor_role="owner"
    )
    u = await _user(db)
    _login(client, u.id, {"onboard_code": code})
    page = client.get("/onboard/home").text
    assert "invited to join <strong>Acme</strong>" in page and "editor" in page
    assert "Create a workspace" in page
    assert 'name="csrf" value="tok"' in page


@pytest.mark.asyncio
async def test_home_invalid_code_notice_and_cap_text(client, db, monkeypatch):
    monkeypatch.setattr(settings, "self_serve_max_workspaces_per_user", 0)
    u = await _user(db)
    _login(client, u.id, {"onboard_code": "bogus"})
    page = client.get("/onboard/home").text
    assert "invalid, expired, or already used" in page
    assert "Create a workspace" not in page


@pytest.mark.asyncio
async def test_home_already_member_shows_continue(client, db):
    owner = await _user(db, "o@example.com")
    ws = Workspace(name="Acme", slug="acme", created_by=owner.id)
    db.add(ws)
    await db.flush()
    db.add(WorkspaceMembership(workspace_id=ws.id, user_id=owner.id, role="owner"))
    await db.commit()
    _, code = await invitation_service.create(
        db, workspace_id=ws.id, role="viewer", created_by=owner.id, actor_role="owner"
    )
    _login(client, owner.id, {"onboard_code": code})
    page = client.get("/onboard/home").text
    assert "already a member of <strong>Acme</strong>" in page
    assert "Join Acme" not in page


@pytest.mark.asyncio
async def test_home_locked_invite_for_other_email_shows_invalid(client, db):
    owner = await _user(db, "o@example.com")
    ws = Workspace(name="Acme", slug="acme", created_by=owner.id)
    db.add(ws)
    await db.flush()
    db.add(WorkspaceMembership(workspace_id=ws.id, user_id=owner.id, role="owner"))
    await db.commit()
    _, code = await invitation_service.create(
        db,
        workspace_id=ws.id,
        role="viewer",
        created_by=owner.id,
        actor_role="owner",
        email="someone.else@example.com",
    )
    u = await _user(db, "different@example.com")
    _login(client, u.id, {"onboard_code": code})
    page = client.get("/onboard/home").text
    assert "invalid, expired, or already used" in page
    assert "invited to join" not in page


def test_logout_without_session_redirects_not_403(client):
    # session-before-csrf: no cookie at all must never reach the CSRF check.
    r = client.post("/onboard/logout", data={"csrf": "whatever"})
    assert r.status_code == 303 and r.headers["location"].endswith("/onboard/login")


@pytest.mark.asyncio
async def test_done_and_logout(client, db):
    u = await _user(db)
    _login(
        client,
        u.id,
        {
            "onboard_result": {"kind": "joined", "workspace": "Acme"},
            "onboard_return_to": "https://app.example/login",
        },
    )
    page = client.get("/onboard/done")
    assert page.status_code == 200 and "You joined <strong>Acme</strong>" in page.text
    assert 'href="https://app.example/login"' in page.text
    assert client.post("/onboard/logout", data={"csrf": "wrong"}).status_code == 403
    # non-ASCII token: compare_digest on str would TypeError -> 500
    assert client.post("/onboard/logout", data={"csrf": "\u00e9"}).status_code == 403
    r = client.post("/onboard/logout", data={"csrf": "tok"})
    # POST lands on the 200 chooser, not the auto-redirecting /onboard (CSP form-action)
    assert r.status_code == 303 and r.headers["location"].endswith("/onboard/login")
    assert client.get("/onboard/home").status_code == 302


@pytest.mark.asyncio
async def test_proxy_callback_keeps_onboard_session(client, db):
    """/auth/callback pops only its own keys: a proxy login in another tab must
    not wipe an in-flight hosted sign-in (it used to session.clear())."""
    from src.api import auth_routes
    from src.api.auth_routes import router as auth_router

    client.app.include_router(auth_router)
    u = await _user(db)
    _login(client, u.id)
    userinfo = {"sub": "g-9", "email": u.email, "email_verified": True, "name": "U"}
    with (
        patch.object(auth_routes, "get_configured_providers", return_value=["google"]),
        patch.object(
            auth_routes.oauth, "create_client", return_value=_fake_oidc_client(userinfo)
        ),
        patch.object(signal_service, "on_login_success", new_callable=AsyncMock),
    ):
        r = client.get("/auth/callback/google")
    assert r.status_code == 400 and "Session Expired" in r.text  # no proxy round here
    assert client.get("/onboard/home").status_code == 200  # onboard_user_id survived


@pytest.mark.asyncio
async def test_no_store_and_html_csp_marker_on_pages(client, db):
    u = await _user(db)
    _login(client, u.id)
    r = client.get("/onboard/home")
    assert (
        r.headers.get("X-CSP-Override") == "html-page"
    )  # consumed by SecurityHeadersMiddleware in the real app
