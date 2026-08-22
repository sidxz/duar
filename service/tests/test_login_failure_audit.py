"""Tests: failed sign-ins write an admin-visible ActivityLog row.

``_log_login_failure`` is the single funnel every callback failure path routes
through — it must roll back partial flow state, commit only the audit row, and
never raise into the error path it decorates.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.auth_routes import _log_login_failure


@pytest.fixture(autouse=True)
def _no_signal_side_effects():
    """Isolate the audit funnel from the stuffing counter (needs Redis; its
    fail-open path rolls back the session). The hookup itself is covered in
    test_signals.py."""
    with patch(
        "src.api.auth_routes.signal_service.on_login_failure", new_callable=AsyncMock
    ):
        yield


def _fake_request(ip: str = "203.0.113.9", ua: str = "TestUA/1.0") -> MagicMock:
    request = MagicMock()
    request.client.host = ip
    request.headers = {"user-agent": ua}
    return request


@pytest.mark.asyncio
async def test_writes_login_failed_row():
    db = AsyncMock()
    with patch(
        "src.services.activity_service.log_activity", new_callable=AsyncMock
    ) as log_activity:
        await _log_login_failure(
            db,
            _fake_request(),
            provider="google",
            reason="org_not_permitted",
            email="eve@evil.example",
        )

    kwargs = log_activity.await_args.kwargs
    assert kwargs["action"] == "login_failed"
    assert kwargs["target_type"] == "system"
    assert kwargs["target_id"] == uuid.UUID(int=0)
    assert kwargs["detail"]["provider"] == "google"
    assert kwargs["detail"]["reason"] == "org_not_permitted"
    assert kwargs["detail"]["ip"] == "203.0.113.9"
    assert kwargs["detail"]["user_agent"] == "TestUA/1.0"
    assert kwargs["detail"]["email"] == "eve@evil.example"

    # Partial flow state discarded BEFORE the audit row is added, then committed.
    ops = [c[0] for c in db.mock_calls if c[0] in ("rollback", "commit")]
    assert ops == ["rollback", "commit"]


@pytest.mark.asyncio
async def test_admin_flow_uses_admin_action():
    db = AsyncMock()
    with patch(
        "src.services.activity_service.log_activity", new_callable=AsyncMock
    ) as log_activity:
        await _log_login_failure(
            db, _fake_request(), provider="entraid", reason="not_admin", flow="admin"
        )
    assert log_activity.await_args.kwargs["action"] == "admin_login_failed"


@pytest.mark.asyncio
async def test_error_type_recorded_for_callback_errors():
    db = AsyncMock()
    with patch(
        "src.services.activity_service.log_activity", new_callable=AsyncMock
    ) as log_activity:
        await _log_login_failure(
            db,
            _fake_request(),
            provider="github",
            reason="callback_error",
            error_type="MismatchingStateError",
        )
    assert (
        log_activity.await_args.kwargs["detail"]["error_type"]
        == "MismatchingStateError"
    )


@pytest.mark.asyncio
async def test_never_raises_into_the_error_path():
    """Audit failure (DB down, etc.) must not mask the original error response."""
    db = AsyncMock()
    db.commit.side_effect = RuntimeError("db down")
    with patch("src.services.activity_service.log_activity", new_callable=AsyncMock):
        await _log_login_failure(
            db, _fake_request(), provider="google", reason="callback_error"
        )
    # reaching here without an exception is the assertion
