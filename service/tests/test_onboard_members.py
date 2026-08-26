"""Hosted /onboard pages — workspace management (members, roles, remove, leave, rename)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

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
from src.services import workspace_service
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


@pytest.fixture(autouse=True)
def _no_redis():
    with patch.object(
        workspace_service.token_service,
        "revoke_all_user_tokens",
        new_callable=AsyncMock,
    ) as m:
        yield m


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


async def _ws_with(db, roles: dict[str, str]) -> tuple[Workspace, dict[str, User]]:
    """One workspace named 'Acme' plus one user per ``{label: role}`` entry.

    Each user's email is ``{label}@example.com``; the returned dict is keyed
    by the same label so callers can look users up by it.
    """
    users: dict[str, User] = {}
    ws: Workspace | None = None
    for label, role in roles.items():
        u = User(
            email=f"{label}@example.com",
            name=label.title(),
            organization_id=PUBLIC_ORG_ID,
        )
        db.add(u)
        await db.flush()
        if ws is None:
            ws = Workspace(
                name="Acme", slug=f"acme-{uuid.uuid4().hex[:4]}", created_by=u.id
            )
            db.add(ws)
            await db.flush()
        db.add(WorkspaceMembership(workspace_id=ws.id, user_id=u.id, role=role))
        users[label] = u
    await db.commit()
    assert ws is not None
    return ws, users


def _login(client, user_id, extra=None):
    data = {"onboard_user_id": str(user_id), "onboard_csrf": "tok", **(extra or {})}
    # domain pinned to match what TestClient's cookiejar normalizes server-issued
    # cookies to for the single-label host "testserver" — see test_onboard_invites.py.
    client.cookies.set(
        "session", session_cookie(data, SECRET), domain="testserver.local"
    )


async def _actions(db) -> list[str]:
    return [a for (a,) in (await db.execute(select(ActivityLog.action))).all()]


def test_manage_routes_404_when_flag_off(client, monkeypatch):
    monkeypatch.setattr(settings, "self_serve_enabled", False)
    wid, uid = uuid.uuid4(), uuid.uuid4()
    assert (
        client.post(
            f"/onboard/members/{uid}/role",
            data={"workspace_id": str(wid), "role": "admin", "csrf": "tok"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/onboard/members/{uid}/remove",
            data={"workspace_id": str(wid), "csrf": "tok"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/onboard/workspace/rename",
            data={"workspace_id": str(wid), "name": "x", "csrf": "tok"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/onboard/leave", data={"workspace_id": str(wid), "csrf": "tok"}
        ).status_code
        == 404
    )


@pytest.mark.asyncio
async def test_members_table_lists_roles_and_marks_me(client, db):
    ws, users = await _ws_with(db, {"owner": "owner", "editor": "editor"})
    _login(client, users["owner"].id)
    page = client.get("/onboard/invites").text
    assert "owner@example.com" in page and "editor@example.com" in page
    assert "(you)" in page
    # the editor row's role <select> has the "editor" option selected
    assert '<option value="editor" selected>editor</option>' in page
    # the owner row's role <select> has the "owner" option selected
    assert '<option value="owner" selected>owner</option>' in page


@pytest.mark.asyncio
async def test_owner_promotes_editor_to_admin(client, db, _no_redis):
    ws, users = await _ws_with(db, {"owner": "owner", "editor": "editor"})
    _login(client, users["owner"].id)
    r = client.post(
        f"/onboard/members/{users['editor'].id}/role",
        data={"workspace_id": str(ws.id), "role": "admin", "csrf": "tok"},
    )
    assert r.status_code == 303
    assert (
        r.headers["location"] == f"http://testserver/onboard/invites?workspace={ws.id}"
    )
    assert (
        await workspace_service.get_member_role(db, ws.id, users["editor"].id)
        == "admin"
    )
    assert "member_role_changed" in await _actions(db)
    _no_redis.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_cannot_grant_owner(client, db):
    ws, users = await _ws_with(
        db, {"owner": "owner", "admin": "admin", "editor": "editor"}
    )
    _login(client, users["admin"].id)
    r = client.post(
        f"/onboard/members/{users['editor'].id}/role",
        data={"workspace_id": str(ws.id), "role": "owner", "csrf": "tok"},
    )
    assert r.status_code == 303
    assert (
        "Only workspace owners can grant the owner role"
        in client.get("/onboard/invites").text
    )
    assert (
        await workspace_service.get_member_role(db, ws.id, users["editor"].id)
        == "editor"
    )


@pytest.mark.asyncio
async def test_sole_owner_cannot_demote_self(client, db):
    ws, users = await _ws_with(db, {"owner": "owner"})
    _login(client, users["owner"].id)
    r = client.post(
        f"/onboard/members/{users['owner'].id}/role",
        data={"workspace_id": str(ws.id), "role": "admin", "csrf": "tok"},
    )
    assert r.status_code == 303
    assert (
        "Cannot demote the last workspace owner" in client.get("/onboard/invites").text
    )
    assert (
        await workspace_service.get_member_role(db, ws.id, users["owner"].id) == "owner"
    )


@pytest.mark.asyncio
async def test_invalid_role_value_flashes(client, db):
    ws, users = await _ws_with(db, {"owner": "owner", "editor": "editor"})
    _login(client, users["owner"].id)
    r = client.post(
        f"/onboard/members/{users['editor'].id}/role",
        data={"workspace_id": str(ws.id), "role": "superuser", "csrf": "tok"},
    )
    assert r.status_code == 303
    assert "Invalid role." in client.get("/onboard/invites").text
    assert (
        await workspace_service.get_member_role(db, ws.id, users["editor"].id)
        == "editor"
    )


@pytest.mark.asyncio
async def test_editor_cannot_manage(client, db):
    ws, users = await _ws_with(
        db, {"owner": "owner", "editor": "editor", "viewer": "viewer"}
    )
    _login(client, users["editor"].id)
    r = client.post(
        f"/onboard/members/{users['viewer'].id}/role",
        data={"workspace_id": str(ws.id), "role": "admin", "csrf": "tok"},
    )
    assert r.status_code == 403
    r2 = client.post(
        f"/onboard/members/{users['viewer'].id}/remove",
        data={"workspace_id": str(ws.id), "csrf": "tok"},
    )
    assert r2.status_code == 403


@pytest.mark.asyncio
async def test_role_change_session_before_csrf(client, db):
    ws, users = await _ws_with(db, {"owner": "owner", "editor": "editor"})
    r = client.post(
        f"/onboard/members/{users['editor'].id}/role",
        data={"workspace_id": str(ws.id), "role": "admin", "csrf": "tok"},
    )
    assert r.status_code == 303 and r.headers["location"] == "http://testserver/onboard"
    _login(client, users["owner"].id)
    r2 = client.post(
        f"/onboard/members/{users['editor'].id}/role",
        data={"workspace_id": str(ws.id), "role": "admin", "csrf": "bad"},
    )
    assert r2.status_code == 403


@pytest.mark.asyncio
async def test_leave_session_before_csrf(client, db):
    ws, users = await _ws_with(db, {"owner": "owner", "editor": "editor"})
    r = client.post("/onboard/leave", data={"workspace_id": str(ws.id), "csrf": "tok"})
    assert r.status_code == 303 and r.headers["location"] == "http://testserver/onboard"
    _login(client, users["editor"].id)
    r2 = client.post(
        "/onboard/leave", data={"workspace_id": str(ws.id), "csrf": "nope"}
    )
    assert r2.status_code == 403


@pytest.mark.asyncio
async def test_owner_removes_editor(client, db, _no_redis):
    ws, users = await _ws_with(db, {"owner": "owner", "editor": "editor"})
    _login(client, users["owner"].id)
    r = client.post(
        f"/onboard/members/{users['editor'].id}/remove",
        data={"workspace_id": str(ws.id), "csrf": "tok"},
    )
    assert r.status_code == 303
    assert (
        r.headers["location"] == f"http://testserver/onboard/invites?workspace={ws.id}"
    )
    assert (
        await workspace_service.get_member_role(db, ws.id, users["editor"].id) is None
    )
    assert "member_removed" in await _actions(db)
    _no_redis.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_cannot_remove_owner(client, db):
    ws, users = await _ws_with(db, {"owner": "owner", "admin": "admin"})
    _login(client, users["admin"].id)
    r = client.post(
        f"/onboard/members/{users['owner'].id}/remove",
        data={"workspace_id": str(ws.id), "csrf": "tok"},
    )
    assert r.status_code == 303
    assert (
        "Only workspace owners can remove another owner"
        in client.get("/onboard/invites").text
    )


@pytest.mark.asyncio
async def test_rename(client, db):
    ws, users = await _ws_with(db, {"owner": "owner"})
    _login(client, users["owner"].id)
    r = client.post(
        "/onboard/workspace/rename",
        data={"workspace_id": str(ws.id), "name": "<b>New</b> Name", "csrf": "tok"},
    )
    assert r.status_code == 303
    await db.refresh(ws)
    assert ws.name == "New Name"
    assert "workspace_updated" in await _actions(db)
    assert "New Name" in client.get("/onboard/invites").text

    # A blank <input> value is dropped entirely by form encoding (parse_qsl
    # keep_blank_values=False), turning "name" into a 422 missing-field rather
    # than exercising the empty-after-strip-html guard — send tags that strip
    # to empty instead, matching test_onboard_actions.py's convention.
    r2 = client.post(
        "/onboard/workspace/rename",
        data={"workspace_id": str(ws.id), "name": "<i></i>", "csrf": "tok"},
    )
    assert r2.status_code == 303
    assert "A workspace name is required." in client.get("/onboard/invites").text


@pytest.mark.asyncio
async def test_member_leaves(client, db, _no_redis):
    ws, users = await _ws_with(db, {"owner": "owner", "editor": "editor"})
    _login(client, users["editor"].id)
    r = client.post("/onboard/leave", data={"workspace_id": str(ws.id), "csrf": "tok"})
    assert (
        r.status_code == 303
        and r.headers["location"] == "http://testserver/onboard/home"
    )
    assert (
        await workspace_service.get_member_role(db, ws.id, users["editor"].id) is None
    )
    assert "You left Acme." in client.get("/onboard/home").text
    rows = (await db.execute(select(ActivityLog.action, ActivityLog.detail))).all()
    assert any(a == "member_removed" and d and d.get("left") for a, d in rows)
    _no_redis.assert_awaited_once()


@pytest.mark.asyncio
async def test_sole_owner_cannot_leave(client, db):
    ws, users = await _ws_with(db, {"owner": "owner"})
    _login(client, users["owner"].id)
    r = client.post("/onboard/leave", data={"workspace_id": str(ws.id), "csrf": "tok"})
    assert (
        r.status_code == 303
        and r.headers["location"] == "http://testserver/onboard/home"
    )
    assert "Cannot remove the last workspace owner" in client.get("/onboard/home").text
    assert (
        await workspace_service.get_member_role(db, ws.id, users["owner"].id) == "owner"
    )


@pytest.mark.asyncio
async def test_leave_non_member_flashes(client, db):
    ws, users = await _ws_with(db, {"owner": "owner"})
    _login(client, users["owner"].id)
    r = client.post(
        "/onboard/leave", data={"workspace_id": str(uuid.uuid4()), "csrf": "tok"}
    )
    assert (
        r.status_code == 303
        and r.headers["location"] == "http://testserver/onboard/home"
    )
    # Jinja autoescape turns the apostrophe into &#39; in the rendered flash div.
    assert (
        "You&#39;re not a member of that workspace." in client.get("/onboard/home").text
    )


# ── two-step confirm pages (zero-JS) ───────────────────────────────────


def test_confirm_404_when_flag_off(client, monkeypatch):
    monkeypatch.setattr(settings, "self_serve_enabled", False)
    r = client.get(f"/onboard/confirm?action=leave&workspace={uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_confirm_leave_renders_form_for_member(client, db):
    ws, users = await _ws_with(db, {"owner": "owner", "editor": "editor"})
    _login(client, users["editor"].id)
    r = client.get(f"/onboard/confirm?action=leave&workspace={ws.id}")
    assert r.status_code == 200
    assert "Leave Acme?" in r.text
    assert 'action="http://testserver/onboard/leave"' in r.text
    assert f'name="workspace_id" value="{ws.id}"' in r.text
    assert 'name="csrf" value="tok"' in r.text
    # the home page only links here — no leave form on the list itself
    home = client.get("/onboard/home").text
    assert f"/onboard/confirm?action=leave&amp;workspace={ws.id}" in home
    assert 'action="http://testserver/onboard/leave"' not in home


@pytest.mark.asyncio
async def test_confirm_leave_non_member_redirects_home(client, db):
    ws, users = await _ws_with(db, {"owner": "owner"})
    outsider = User(email="x@example.com", name="X", organization_id=PUBLIC_ORG_ID)
    db.add(outsider)
    await db.commit()
    _login(client, outsider.id)
    r = client.get(f"/onboard/confirm?action=leave&workspace={ws.id}")
    assert r.status_code == 303 and r.headers["location"].endswith("/onboard/home")


@pytest.mark.asyncio
async def test_confirm_remove_owner_sees_target(client, db):
    ws, users = await _ws_with(db, {"owner": "owner", "editor": "editor"})
    _login(client, users["owner"].id)
    eid = users["editor"].id
    r = client.get(f"/onboard/confirm?action=remove&workspace={ws.id}&user={eid}")
    assert r.status_code == 200
    assert "Remove Editor?" in r.text and "editor@example.com" in r.text
    assert f'action="http://testserver/onboard/members/{eid}/remove"' in r.text


@pytest.mark.asyncio
async def test_confirm_remove_denied_for_editor(client, db):
    ws, users = await _ws_with(db, {"owner": "owner", "editor": "editor"})
    _login(client, users["editor"].id)
    oid = users["owner"].id
    r = client.get(f"/onboard/confirm?action=remove&workspace={ws.id}&user={oid}")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_confirm_rejects_unknown_action_and_bad_ids(client, db):
    ws, users = await _ws_with(db, {"owner": "owner"})
    _login(client, users["owner"].id)
    assert (
        client.get(f"/onboard/confirm?action=nuke&workspace={ws.id}").status_code == 404
    )
    assert client.get("/onboard/confirm?action=leave&workspace=nope").status_code == 404


@pytest.mark.asyncio
async def test_admin_sees_owner_row_as_text_not_select(client, db):
    ws, users = await _ws_with(db, {"owner": "owner", "admin": "admin"})
    _login(client, users["admin"].id)
    page = client.get(f"/onboard/invites?workspace={ws.id}").text
    # no role <select> (and no Remove link) for the owner's row when the actor is an admin
    assert '<option value="owner"' not in page
    assert f"user={users['owner'].id}" not in page
    # the admin's own row is still editable
    assert '<option value="admin" selected>admin</option>' in page


@pytest.mark.asyncio
async def test_confirm_remove_never_renders_non_members(client, db):
    ws, users = await _ws_with(db, {"owner": "owner"})
    outsider = User(
        email="outsider@example.com", name="Outsider", organization_id=PUBLIC_ORG_ID
    )
    db.add(outsider)
    await db.commit()
    _login(client, users["owner"].id)
    # a non-member UUID must not turn the page into a user-directory oracle
    r = client.get(
        f"/onboard/confirm?action=remove&workspace={ws.id}&user={outsider.id}"
    )
    assert r.status_code == 303 and "outsider@example.com" not in r.text
    # ...and neither must a missing one
    r = client.get(f"/onboard/confirm?action=remove&workspace={ws.id}")
    assert r.status_code == 303 and r.headers["location"].endswith(
        f"/onboard/invites?workspace={ws.id}"
    )


@pytest.mark.asyncio
async def test_confirm_remove_admin_cannot_target_owner(client, db):
    ws, users = await _ws_with(db, {"owner": "owner", "admin": "admin"})
    _login(client, users["admin"].id)
    oid = users["owner"].id
    r = client.get(f"/onboard/confirm?action=remove&workspace={ws.id}&user={oid}")
    assert r.status_code == 303 and "owner@example.com" not in r.text
