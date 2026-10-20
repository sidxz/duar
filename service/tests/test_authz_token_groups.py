"""AuthZ-mode tokens carry the caller's workspace groups, so group shares resolve.

Real SQLite DB behind get_db: /authz/resolve mints the token (IdP validation +
user provisioning mocked), then the same token drives /permissions/check and
/permissions/accessible against a resource shared only with the user's group.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.api import authz_routes
from src.api.authz_routes import router as authz_router
from src.api.dependencies import (
    ServiceKeyContext,
    require_service_context,
    require_service_key,
)
from src.api.permission_routes import router as permission_router
from src.auth.jwt import _AUD_AUTHZ, decode_token
from src.database import get_db
from src.middleware.rate_limit import limiter
from src.models.group import Group, GroupMembership
from src.models.organization import Organization
from src.models.permission import ResourcePermission, ResourceShare
from src.models.user import User
from src.models.workspace import Workspace, WorkspaceMembership
from src.services import token_service
from tests.self_serve_fixtures import make_engine

SVC, TYPE = "docs", "document"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    original = limiter.enabled
    limiter.enabled = False

    async def _false(_arg):
        return False

    monkeypatch.setattr(token_service, "is_access_token_blacklisted", _false)
    monkeypatch.setattr(token_service, "is_user_deactivated", _false)
    yield
    limiter.enabled = original


@pytest_asyncio.fixture
async def seeded():
    engine = await make_engine()
    async with AsyncSession(engine, expire_on_commit=False) as db:
        org = Organization(slug="tamu", name="TAMU", is_public=False, enabled=True)
        db.add(org)
        await db.flush()
        user = User(email="alice@tamu.edu", name="Alice", organization_id=org.id)
        owner = User(email="bob@tamu.edu", name="Bob", organization_id=org.id)
        ws = Workspace(slug="lab", name="Lab")
        other_ws = Workspace(slug="other", name="Other")
        db.add_all([user, owner, ws, other_ws])
        await db.flush()
        group = Group(workspace_id=ws.id, name="chemists")
        other_group = Group(workspace_id=other_ws.id, name="elsewhere")
        db.add_all(
            [
                group,
                other_group,
                WorkspaceMembership(workspace_id=ws.id, user_id=user.id, role="viewer"),
            ]
        )
        await db.flush()
        perm = ResourcePermission(
            service_name=SVC,
            resource_type=TYPE,
            resource_id=uuid.uuid4(),
            workspace_id=ws.id,
            owner_id=owner.id,
            visibility="private",
        )
        db.add_all(
            [
                GroupMembership(group_id=group.id, user_id=user.id),
                GroupMembership(group_id=other_group.id, user_id=user.id),
                perm,
            ]
        )
        await db.flush()
        db.add(
            ResourceShare(
                resource_permission_id=perm.id,
                grantee_type="group",
                grantee_id=group.id,
                permission="view",
            )
        )
        await db.commit()
        yield {
            "db": db,
            "org": org,
            "user": user,
            "ws": ws,
            "group": group,
            "other_group": other_group,
            "resource_id": perm.resource_id,
        }
    await engine.dispose()


@pytest.fixture
def client(seeded, monkeypatch):
    async def _validate(token, provider, expected_nonce=None, expected_audiences=None):
        return {"sub": "google|alice", "email": "alice@tamu.edu", "name": "Alice"}

    async def _resolve_org(_db, _email):
        return seeded["org"]

    async def _find_user(**_kw):
        return seeded["user"]

    async def _allow(_db, _ws, _org_id):
        return True

    async def _no_actions(_db, _user_id, _scope, _ws_id):
        return []

    monkeypatch.setattr(authz_routes, "validate_idp_token", _validate)
    monkeypatch.setattr(authz_routes, "bind_identity", lambda *a, **k: None)
    monkeypatch.setattr(authz_routes, "log_security", lambda *a, **k: None)
    monkeypatch.setattr(authz_routes, "get_user_actions", _no_actions)
    monkeypatch.setattr(
        authz_routes.organization_service, "resolve_organization", _resolve_org
    )
    monkeypatch.setattr(
        authz_routes.organization_service, "workspace_allows_org", _allow
    )
    monkeypatch.setattr(authz_routes.auth_service, "find_or_create_user", _find_user)

    app = FastAPI()
    app.include_router(authz_router)
    app.include_router(permission_router)
    ctx = ServiceKeyContext(service_name=SVC)
    app.dependency_overrides[require_service_context] = lambda: ctx
    app.dependency_overrides[require_service_key] = lambda: ctx

    async def _db():
        yield seeded["db"]

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def _mint(client, workspace_id) -> str:
    resp = client.post(
        "/authz/resolve",
        json={
            "idp_token": "x",
            "provider": "google",
            "workspace_id": str(workspace_id),
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["authz_token"]


@pytest.mark.asyncio
async def test_authz_token_carries_only_this_workspace_groups(client, seeded):
    payload = decode_token(_mint(client, seeded["ws"].id), audience=_AUD_AUTHZ)
    assert payload["groups"] == [str(seeded["group"].id)]
    assert str(seeded["other_group"].id) not in payload["groups"]


@pytest.mark.asyncio
async def test_group_shared_resource_resolves_for_authz_caller(client, seeded):
    headers = {"Authorization": f"Bearer {_mint(client, seeded['ws'].id)}"}
    check = client.post(
        "/permissions/check",
        headers=headers,
        json={
            "checks": [
                {
                    "service_name": SVC,
                    "resource_type": TYPE,
                    "resource_id": str(seeded["resource_id"]),
                    "action": "view",
                }
            ]
        },
    )
    assert check.status_code == 200, check.text
    assert check.json()["results"][0]["allowed"] is True

    accessible = client.post(
        "/permissions/accessible",
        headers=headers,
        json={
            "service_name": SVC,
            "resource_type": TYPE,
            "action": "view",
            "workspace_id": str(seeded["ws"].id),
        },
    )
    assert accessible.status_code == 200, accessible.text
    assert str(seeded["resource_id"]) in accessible.json()["resource_ids"]
