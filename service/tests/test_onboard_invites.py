"""Hosted /onboard pages — part C (inviter side)."""

from __future__ import annotations

import hashlib
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

from src.api import onboard_routes
from src.api.onboard_routes import router as onboard_router
from src.config import settings
from src.database import get_db
from src.middleware.rate_limit import limiter
from src.models.activity import ActivityLog
from src.models.invitation import WorkspaceInvitation
from src.models.organization import Organization
from src.models.user import User
from src.models.workspace import Workspace, WorkspaceMembership
from tests.self_serve_fixtures import make_engine, session_cookie

SECRET = "test-secret"
PUBLIC_ORG_ID = uuid.UUID("00000000-0000-0000-0000-00000000000a")


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


async def _user_with_ws(db, role="owner"):
    u = User(
        email=f"{uuid.uuid4().hex[:6]}@example.com",
        name="U",
        organization_id=PUBLIC_ORG_ID,
    )
    db.add(u)
    await db.flush()
    ws = Workspace(name="Acme", slug=f"acme-{uuid.uuid4().hex[:4]}", created_by=u.id)
    db.add(ws)
    await db.flush()
    db.add(WorkspaceMembership(workspace_id=ws.id, user_id=u.id, role=role))
    await db.commit()
    return u, ws


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


@pytest.mark.asyncio
async def test_invites_without_session_sets_next_and_redirects(client, db):
    wid = uuid.uuid4()
    r = client.get("/onboard/invites", params={"workspace": str(wid)})
    assert r.status_code == 302 and r.headers["location"].endswith("/onboard")
    from base64 import b64decode
    import json
    from itsdangerous import TimestampSigner

    sess = json.loads(
        b64decode(TimestampSigner(SECRET).unsign(client.cookies["session"]))
    )
    assert sess["onboard_next"] == f"http://testserver/onboard/invites?workspace={wid}"


@pytest.mark.asyncio
async def test_invites_validates_return_to(client, db):
    u, _ = await _user_with_ws(db)
    _login(client, u.id)
    with patch.object(
        onboard_routes, "service_app_origin_allowed", AsyncMock(return_value=False)
    ):
        assert (
            client.get(
                "/onboard/invites", params={"return_to": "https://evil.example/"}
            ).status_code
            == 400
        )


@pytest.mark.asyncio
async def test_invites_lists_only_admin_workspaces(client, db):
    u, ws = await _user_with_ws(db, role="viewer")
    _login(client, u.id)
    assert "not an owner or admin" in client.get("/onboard/invites").text
    u2, ws2 = await _user_with_ws(db, role="admin")
    _login(client, u2.id)
    assert "New invitation for Acme" in client.get("/onboard/invites").text


@pytest.mark.asyncio
async def test_create_invite_shows_link_once_and_stores_hash(client, db):
    u, ws = await _user_with_ws(db)
    _login(client, u.id, {"onboard_return_to": "https://app.example/login"})
    r = client.post(
        "/onboard/invites",
        data={
            "workspace_id": str(ws.id),
            "role": "editor",
            "email": " Bob@Example.com ",
            "csrf": "tok",
        },
    )
    assert r.status_code == 303
    page = client.get("/onboard/invites").text
    assert "Shown once" in page
    link = page.split('id="link" readonly value="')[1].split('"')[0]
    assert (
        link.startswith("http://testserver/onboard?code=")
        and "return_to=https%3A%2F%2Fapp.example%2Flogin" in link
    )
    code = link.split("code=")[1].split("&")[0]
    inv = await db.scalar(select(WorkspaceInvitation))
    assert inv.code_hash == hashlib.sha256(code.encode()).hexdigest()
    assert inv.email == "bob@example.com" and inv.role == "editor"
    # second render: gone
    assert "Shown once" not in client.get("/onboard/invites").text
    assert "bob@example.com" in client.get("/onboard/invites").text
    actions = [a for (a,) in (await db.execute(select(ActivityLog.action))).all()]
    assert "invitation_created" in actions
    details = [d for (d,) in (await db.execute(select(ActivityLog.detail))).all()]
    assert all(code not in str(d) and inv.code_hash not in str(d) for d in details)


@pytest.mark.asyncio
async def test_create_invite_requires_admin_of_that_workspace(client, db):
    u, ws = await _user_with_ws(db, role="editor")
    _login(client, u.id)
    r = client.post(
        "/onboard/invites",
        data={"workspace_id": str(ws.id), "role": "viewer", "csrf": "tok"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_create_invite_bad_email_flashes(client, db):
    u, ws = await _user_with_ws(db)
    _login(client, u.id)
    r = client.post(
        "/onboard/invites",
        data={
            "workspace_id": str(ws.id),
            "role": "viewer",
            "email": "nope",
            "csrf": "tok",
        },
    )
    assert r.status_code == 303
    assert "Invalid email" in client.get("/onboard/invites").text


@pytest.mark.asyncio
async def test_revoke(client, db):
    u, ws = await _user_with_ws(db)
    _login(client, u.id)
    client.post(
        "/onboard/invites",
        data={"workspace_id": str(ws.id), "role": "viewer", "csrf": "tok"},
    )
    inv = await db.scalar(select(WorkspaceInvitation))
    assert (
        client.post(
            f"/onboard/invites/{inv.id}/revoke", data={"csrf": "bad"}
        ).status_code
        == 403
    )
    r = client.post(f"/onboard/invites/{inv.id}/revoke", data={"csrf": "tok"})
    assert r.status_code == 303
    await db.refresh(inv)
    assert inv.revoked_at is not None
    outsider, _ = await _user_with_ws(db)
    _login(client, outsider.id)
    r = client.post(f"/onboard/invites/{uuid.uuid4()}/revoke", data={"csrf": "tok"})
    assert r.status_code == 303  # unknown → flash, no disclosure
