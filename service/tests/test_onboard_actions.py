"""Hosted /onboard pages — part B (join, create)."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

from src.api.onboard_routes import router as onboard_router
from src.config import settings
from src.database import get_db
from src.middleware.rate_limit import limiter
from src.models.activity import ActivityLog
from src.models.organization import Organization
from src.models.user import User
from src.models.workspace import Workspace, WorkspaceMembership
from src.services import invitation_service
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
    monkeypatch.setattr(settings, "self_serve_max_workspaces_per_user", 1)
    monkeypatch.setattr(settings, "self_serve_max_creates_per_hour", 30)
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


async def _user(db, email="u@example.com") -> User:
    u = User(email=email, name="U", organization_id=PUBLIC_ORG_ID)
    db.add(u)
    await db.commit()
    return u


async def _invite(db):
    owner = await _user(db, f"o-{uuid.uuid4().hex[:4]}@example.com")
    ws = Workspace(
        name="Acme", slug=f"acme-{uuid.uuid4().hex[:4]}", created_by=owner.id
    )
    db.add(ws)
    await db.flush()
    db.add(WorkspaceMembership(workspace_id=ws.id, user_id=owner.id, role="owner"))
    await db.commit()
    _, code = await invitation_service.create(
        db, workspace_id=ws.id, role="editor", created_by=owner.id, actor_role="owner"
    )
    return ws, code


def _login(client, user_id, extra=None):
    client.cookies.set(
        "session",
        session_cookie(
            {"onboard_user_id": str(user_id), "onboard_csrf": "tok", **(extra or {})},
            SECRET,
        ),
        domain="testserver.local",
    )


async def _actions(db):
    return [
        a
        for (a,) in (
            await db.execute(
                select(ActivityLog.action).order_by(ActivityLog.created_at)
            )
        ).all()
    ]


# ── join ──────────────────────────────────────────────────────────────


def test_join_without_session_redirects_not_403(client):
    r = client.post("/onboard/join", data={"code": "x", "csrf": "whatever"})
    assert r.status_code == 303 and r.headers["location"].endswith("/onboard")


@pytest.mark.asyncio
async def test_join_wrong_csrf_403(client, db):
    u = await _user(db)
    _login(client, u.id)
    assert (
        client.post("/onboard/join", data={"code": "x", "csrf": "nope"}).status_code
        == 403
    )


@pytest.mark.asyncio
async def test_join_happy_path_accepts_full_link(client, db):
    ws, code = await _invite(db)
    u = await _user(db)
    _login(client, u.id)
    r = client.post(
        "/onboard/join",
        data={
            "code": f"http://testserver/onboard?code={code}&return_to=x",
            "csrf": "tok",
        },
    )
    assert r.status_code == 303 and r.headers["location"].endswith("/onboard/done")
    role = await db.scalar(
        select(WorkspaceMembership.role).where(WorkspaceMembership.user_id == u.id)
    )
    assert role == "editor"
    assert "invitation_accepted" in await _actions(db)
    assert "You joined <strong>Acme</strong>" in client.get("/onboard/done").text


@pytest.mark.asyncio
async def test_join_invalid_code_flashes_generic(client, db):
    u = await _user(db)
    _login(client, u.id)
    r = client.post("/onboard/join", data={"code": "bogus", "csrf": "tok"})
    assert r.status_code == 303 and r.headers["location"].endswith("/onboard/home")
    assert "invalid, expired, or already used" in client.get("/onboard/home").text
    assert "invitation_rejected" in await _actions(db)


# ── create ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_happy_path(client, db):
    u = await _user(db)
    _login(client, u.id)
    r = client.post("/onboard/create", data={"name": "<b>My</b> Lab", "csrf": "tok"})
    assert r.status_code == 303 and r.headers["location"].endswith("/onboard/done")
    ws = await db.scalar(select(Workspace).where(Workspace.created_by == u.id))
    assert ws.name == "My Lab" and ws.slug.startswith("my-lab-")
    assert "workspace_created" in await _actions(db)
    assert "You created <strong>My Lab</strong>" in client.get("/onboard/done").text


@pytest.mark.asyncio
async def test_create_cap_flashes_and_audits(client, db):
    u = await _user(db)
    _login(client, u.id)
    client.post("/onboard/create", data={"name": "One", "csrf": "tok"})
    r = client.post("/onboard/create", data={"name": "Two", "csrf": "tok"})
    assert r.status_code == 303 and r.headers["location"].endswith("/onboard/home")
    assert "limit" in client.get("/onboard/home").text.lower()
    assert "self_serve_denied" in await _actions(db)


@pytest.mark.asyncio
async def test_create_throttled_is_429_page(client, db, monkeypatch):
    monkeypatch.setattr(settings, "self_serve_max_creates_per_hour", 0)
    u = await _user(db)
    _login(client, u.id)
    r = client.post("/onboard/create", data={"name": "One", "csrf": "tok"})
    assert r.status_code == 429 and "Too many workspaces" in r.text


@pytest.mark.asyncio
async def test_create_empty_name_flashes(client, db):
    u = await _user(db)
    _login(client, u.id)
    r = client.post("/onboard/create", data={"name": "<i></i>", "csrf": "tok"})
    assert r.status_code == 303
    assert "name" in client.get("/onboard/home").text.lower()
