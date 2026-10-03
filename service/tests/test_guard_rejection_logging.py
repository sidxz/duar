"""Tranche C log-coverage: auth-guard denials, logout symmetry, and the
PATCH /admin/users audit fixes.

- every 401/403 raised by a dependency guard emits auth.guard.rejected
  (info level for routine missing/expired credentials, warning for sharp
  signals like not-admin, CSRF, cross-service replay)
- user/admin logout write activity rows balancing the login rows
- _log_login_failure is the single emit point for auth.login.failed
  (both channels, both flows)
- admin_service.update_user returns one audit action per ACTUAL change
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from src.api import dependencies
from src.api.dependencies import (
    CurrentUser,
    ServiceKeyContext,
    get_current_user,
    get_user_for_service_call,
    require_admin,
    verify_service_scope,
)
from src.database import get_db

USER_ID = uuid.uuid4()
WS_ID = uuid.uuid4()


class _Req:
    def __init__(self, cookies=None, method="GET", headers=None):
        self.cookies = cookies or {}
        self.method = method
        self.headers = headers or {}


def _events(logs, name="auth.guard.rejected"):
    return [e for e in logs if e.get("event") == name]


def _stub_hygiene(monkeypatch):
    from src.services import token_service

    async def _false(_arg):
        return False

    monkeypatch.setattr(token_service, "is_access_token_blacklisted", _false)
    monkeypatch.setattr(token_service, "is_user_deactivated", _false)


# ---------------------------------------------------------------------------
# Guard rejections
# ---------------------------------------------------------------------------


def test_service_scope_mismatch_emits_event():
    ctx = ServiceKeyContext(service_name="notes")
    with capture_logs() as logs:
        with pytest.raises(HTTPException) as exc:
            verify_service_scope(ctx, "crm")
    assert exc.value.status_code == 403
    evt = _events(logs)[0]
    assert evt["reason"] == "service_scope_mismatch"
    assert evt["caller_service"] == "notes"
    assert evt["requested_service"] == "crm"
    assert evt["category"] == "security"


@pytest.mark.asyncio
async def test_missing_bearer_is_info_level():
    with capture_logs() as logs:
        with pytest.raises(HTTPException) as exc:
            await get_current_user(_Req())
    assert exc.value.status_code == 401
    evt = _events(logs)[0]
    assert evt["reason"] == "missing_token"
    assert evt["guard"] == "get_current_user"
    assert evt["log_level"] == "info"  # routine — must not page anyone


@pytest.mark.asyncio
async def test_require_admin_not_admin_emits_event(monkeypatch):
    monkeypatch.setattr(
        dependencies,
        "decode_token",
        lambda *_a, **_k: {"admin": False, "sub": str(USER_ID)},
    )
    with capture_logs() as logs:
        with pytest.raises(HTTPException) as exc:
            await require_admin(_Req(cookies={"admin_token": "x"}), db=None)
    assert exc.value.status_code == 403
    evt = _events(logs)[0]
    assert evt["reason"] == "not_admin"
    assert evt["actor"] == str(USER_ID)
    assert evt["log_level"] == "warning"


@pytest.mark.asyncio
async def test_require_admin_csrf_missing_emits_event(monkeypatch):
    monkeypatch.setattr(
        dependencies,
        "decode_token",
        lambda *_a, **_k: {"admin": True, "sub": str(USER_ID)},
    )
    _stub_hygiene(monkeypatch)
    db = MagicMock()

    async def _get(_model, _pk):
        return SimpleNamespace(is_active=True, is_admin=True)

    db.get = _get
    with capture_logs() as logs:
        with pytest.raises(HTTPException) as exc:
            await require_admin(
                _Req(cookies={"admin_token": "x"}, method="POST"), db=db
            )
    assert exc.value.status_code == 403
    evt = _events(logs)[0]
    assert evt["reason"] == "csrf_header_missing"
    assert evt["actor"] == str(USER_ID)


@pytest.mark.asyncio
async def test_authz_cross_service_replay_emits_event(monkeypatch):
    monkeypatch.setattr(
        dependencies,
        "decode_token",
        lambda *_a, **_k: {
            "type": "authz",
            "sub": str(USER_ID),
            "wid": str(WS_ID),
            "wrole": "editor",
            "svc": "crm",
        },
    )
    _stub_hygiene(monkeypatch)
    with capture_logs() as logs:
        with pytest.raises(HTTPException) as exc:
            await get_user_for_service_call(
                _Req(headers={"Authorization": "Bearer x"}),
                svc_ctx=ServiceKeyContext(service_name="notes"),
            )
    assert exc.value.status_code == 403
    evt = _events(logs)[0]
    assert evt["reason"] == "authz_service_mismatch"
    assert evt["caller_service"] == "notes"
    assert evt["token_svc"] == "crm"
    assert evt["actor"] == str(USER_ID)


# ---------------------------------------------------------------------------
# Logout symmetry
# ---------------------------------------------------------------------------


@pytest.fixture
def activity(monkeypatch):
    from src.services import activity_service

    recorded = []

    async def _log(_db, **kw):
        recorded.append(kw)

    monkeypatch.setattr(activity_service, "log_activity", _log)
    return recorded


class _FakeDB:
    async def commit(self):
        pass


def _auth_app():
    from src.api.auth_routes import router as auth_router

    app = FastAPI()
    app.include_router(auth_router)

    async def _db():
        yield _FakeDB()

    app.dependency_overrides[get_db] = _db
    return app


def test_user_logout_writes_activity_row(monkeypatch, activity):
    from src.api import auth_routes
    from src.services import token_service

    async def _revoke(_uid):
        return 0

    monkeypatch.setattr(token_service, "revoke_all_user_tokens", _revoke)
    app = _auth_app()
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=USER_ID, workspace_id=WS_ID, workspace_role="editor", groups=[]
    )
    with capture_logs() as logs:
        resp = TestClient(app).post(
            "/auth/logout", headers={"Authorization": "Bearer garbage"}
        )
    assert resp.status_code == 200
    assert activity[0]["action"] == "user_logout"
    assert activity[0]["target_id"] == USER_ID
    assert activity[0]["workspace_id"] == WS_ID
    assert _events(logs, "auth.token.revoked")  # stream event kept
    _ = auth_routes  # imported for parity with sibling tests


def test_admin_logout_writes_activity_row(monkeypatch, activity):
    from src.services import token_service

    async def _blacklist(_jti, _exp):
        pass

    monkeypatch.setattr(token_service, "blacklist_access_token", _blacklist)
    app = _auth_app()
    app.dependency_overrides[require_admin] = lambda: {
        "sub": str(USER_ID),
        "jti": "j1",
        "exp": 9999999999,
    }
    with capture_logs() as logs:
        resp = TestClient(app).post("/auth/admin/logout")
    assert resp.status_code == 200
    assert activity[0]["action"] == "admin_logout"
    assert activity[0]["target_id"] == USER_ID
    revoked = _events(logs, "auth.token.revoked")
    assert revoked and revoked[0]["reason"] == "admin_logout"


# ---------------------------------------------------------------------------
# Login-failure single emit point
# ---------------------------------------------------------------------------


class _FailureDB:
    sync_session = None

    def __init__(self):
        self.added = []

    async def rollback(self):
        pass

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        pass


@pytest.mark.asyncio
async def test_log_login_failure_emits_stream_and_row():
    from src.api.auth_routes import _log_login_failure

    req = _Req(headers={"user-agent": "pytest"})
    req.client = None  # get_client_ip fallback
    db = _FailureDB()
    with capture_logs() as logs:
        await _log_login_failure(
            db, req, "google", "not_admin", flow="admin", email="a@evil.example"
        )
    events = _events(logs, "auth.login.failed")
    assert events, "stream event must fire even for admin flow"
    evt = events[0]
    assert evt["reason"] == "not_admin"
    assert evt["flow"] == "admin"
    assert evt["email_domain"] == "evil.example"
    assert db.added and db.added[0].action == "admin_login_failed"


# ---------------------------------------------------------------------------
# PATCH /admin/users — audit per actual change
# ---------------------------------------------------------------------------


class _UpdateDB:
    def __init__(self, user):
        self._user = user

    async def get(self, _model, _pk):
        return self._user

    async def flush(self):
        pass


def _stub_token_side_effects(monkeypatch):
    from src.services import admin_service

    calls = []

    async def _noop(_uid):
        calls.append(_uid)

    monkeypatch.setattr(admin_service.token_service, "revoke_all_user_tokens", _noop)
    monkeypatch.setattr(admin_service.token_service, "mark_user_deactivated", _noop)
    monkeypatch.setattr(admin_service.token_service, "mark_user_activated", _noop)
    return calls


@pytest.mark.asyncio
async def test_update_user_logs_every_actual_change(monkeypatch):
    from src.services import admin_service

    _stub_token_side_effects(monkeypatch)
    user = SimpleNamespace(name="U", is_active=True, is_admin=False)
    _, actions = await admin_service.update_user(
        _UpdateDB(user), uuid.uuid4(), is_active=False, is_admin=True
    )
    # the old if/elif chain would have reported only the promotion
    assert actions == ["user_deactivated", "user_promoted_admin"]
    assert user.is_active is False and user.is_admin is True


@pytest.mark.asyncio
async def test_update_user_noop_change_logs_nothing(monkeypatch):
    from src.services import admin_service

    _stub_token_side_effects(monkeypatch)
    user = SimpleNamespace(name="U", is_active=True, is_admin=True)
    _, actions = await admin_service.update_user(
        _UpdateDB(user), uuid.uuid4(), is_admin=True, name="U"
    )
    assert actions == []  # nothing changed → no phantom audit rows
