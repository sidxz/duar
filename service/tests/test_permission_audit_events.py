"""Entity-ACL surface: denied check verdicts hit the security stream, and
share grants get the log_audit their revoke sibling always had."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from src.api.dependencies import (
    CurrentUser,
    ServiceKeyContext,
    get_user_for_service_call,
    require_service_key,
)
from src.api.permission_routes import router as permission_router
from src.database import get_db
from src.middleware.rate_limit import limiter

WS_ID = uuid.uuid4()
USER_ID = uuid.uuid4()
ALLOWED_RES = uuid.uuid4()
DENIED_RES = uuid.uuid4()


@pytest.fixture(autouse=True)
def _disable_limiter():
    original = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = original


def _build_app():
    app = FastAPI()
    app.include_router(permission_router)
    app.dependency_overrides[require_service_key] = lambda: ServiceKeyContext(
        service_name="notes"
    )
    app.dependency_overrides[get_user_for_service_call] = lambda: CurrentUser(
        user_id=USER_ID, workspace_id=WS_ID, workspace_role="editor", groups=[]
    )

    async def _db():
        yield None

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def _events(logs, name):
    return [e for e in logs if e.get("event") == name]


def test_denied_check_emits_security_event_allowed_does_not(monkeypatch):
    from src.api import permission_routes

    async def _check(_db, *, resource_id, **kw):
        return resource_id == ALLOWED_RES

    monkeypatch.setattr(
        permission_routes.permission_service, "check_permission", _check
    )

    def item(res_id):
        return {
            "service_name": "notes",
            "resource_type": "document",
            "resource_id": str(res_id),
            "action": "edit",
        }

    with capture_logs() as logs:
        resp = _build_app().post(
            "/permissions/check",
            json={"checks": [item(ALLOWED_RES), item(DENIED_RES)]},
        )
    assert resp.status_code == 200
    results = {r["resource_id"]: r["allowed"] for r in resp.json()["results"]}
    assert results == {str(ALLOWED_RES): True, str(DENIED_RES): False}

    events = _events(logs, "permission.check.denied")
    assert len(events) == 1, "exactly the denied item must emit an event"
    evt = events[0]
    assert evt["outcome"] == "denied"
    assert evt["category"] == "security"
    assert evt["actor"] == str(USER_ID)
    assert evt["resource_id"] == str(DENIED_RES)
    assert evt["action"] == "edit"
    assert evt["caller_service"] == "notes"


def test_share_grant_emits_audit_event(monkeypatch):
    from src.api import permission_routes

    permission_id = uuid.uuid4()
    grantee_id = uuid.uuid4()

    async def _get_perm(_db, _pid):
        return SimpleNamespace(
            service_name="notes", workspace_id=WS_ID, owner_id=USER_ID
        )

    async def _share(_db, **kw):
        pass

    monkeypatch.setattr(
        permission_routes.permission_service, "get_permission_by_id", _get_perm
    )
    monkeypatch.setattr(permission_routes.permission_service, "share_resource", _share)

    with capture_logs() as logs:
        resp = _build_app().post(
            f"/permissions/{permission_id}/share",
            json={
                "grantee_type": "user",
                "grantee_id": str(grantee_id),
                "permission": "edit",
            },
        )
    assert resp.status_code == 201
    events = _events(logs, "permission.resource.shared")
    assert events, "an ACL grant must be audited like its revoke sibling"
    evt = events[0]
    assert evt["category"] == "audit"
    assert evt["permission_id"] == str(permission_id)
    assert evt["grantee_id"] == str(grantee_id)
    assert evt["permission"] == "edit"
    assert evt["granted_by"] == str(USER_ID)


def test_rejected_share_not_audited(monkeypatch):
    """A share the service refuses (e.g. grantee outside the workspace) must
    not produce a grant audit event."""
    from src.api import permission_routes

    async def _get_perm(_db, _pid):
        return SimpleNamespace(
            service_name="notes", workspace_id=WS_ID, owner_id=USER_ID
        )

    async def _share(_db, **kw):
        raise ValueError("Grantee is not a member of this workspace")

    monkeypatch.setattr(
        permission_routes.permission_service, "get_permission_by_id", _get_perm
    )
    monkeypatch.setattr(permission_routes.permission_service, "share_resource", _share)

    with capture_logs() as logs:
        resp = _build_app().post(
            f"/permissions/{uuid.uuid4()}/share",
            json={
                "grantee_type": "user",
                "grantee_id": str(uuid.uuid4()),
                "permission": "view",
            },
        )
    assert resp.status_code == 400
    assert _events(logs, "permission.resource.shared") == []
