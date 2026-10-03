"""Tests: auth.* security events are emitted at the correct call sites.

Covers:
- auth.token.reuse_detected (outcome=failure, reason=refresh_reuse) on replay
- auth.token.refreshed (outcome=success) on successful rotation
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from structlog.testing import capture_logs

from src.services import auth_service


# ---------------------------------------------------------------------------
# Temp keypair fixture — avoids requiring keys/ on disk in every environment
# ---------------------------------------------------------------------------


def _write_keypair(tmp_path: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    priv_path = tmp_path / "private.pem"
    pub_path = tmp_path / "public.pem"
    priv_path.write_bytes(priv)
    pub_path.write_bytes(pub)
    return priv_path, pub_path


@pytest.fixture(autouse=True)
def _ephemeral_keys(tmp_path, monkeypatch):
    """Swap JWT key paths for temp files so tests don't depend on keys/ dir."""
    from src.auth import key_provider
    from src.config import settings

    priv_path, pub_path = _write_keypair(tmp_path)
    monkeypatch.setattr(settings, "jwt_private_key_path", priv_path)
    monkeypatch.setattr(settings, "jwt_public_key_path", pub_path)
    key_provider.reset_cache()
    yield
    key_provider.reset_cache()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_user() -> MagicMock:
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "test@example.com"
    user.name = "Test User"
    user.is_active = True
    user.is_admin = False
    user.organization_id = None
    return user


def _fake_db_for_rotate(user, workspace_id) -> MagicMock:
    """Mock DB that lets ``rotate_refresh_token`` progress past its queries."""
    workspace = MagicMock()
    workspace.id = workspace_id
    workspace.slug = "test-ws"

    membership = MagicMock()
    membership.role = "editor"

    db = MagicMock()
    db.get = AsyncMock(side_effect=[user, workspace])

    membership_result = MagicMock()
    membership_result.scalar_one_or_none.return_value = membership

    # workspace_allows_org: empty allowed-org set => open workspace
    allows_org_scalars = MagicMock()
    allows_org_scalars.all.return_value = []
    allows_org_result = MagicMock()
    allows_org_result.scalars.return_value = allows_org_scalars

    groups_result = MagicMock()
    groups_result.all.return_value = []
    db.execute = AsyncMock(
        side_effect=[membership_result, allows_org_result, groups_result]
    )
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_reuse_emits_reuse_detected_event():
    """Replaying a consumed refresh token must emit auth.token.reuse_detected
    with outcome=failure and reason=refresh_reuse.
    """
    from src.auth.jwt import create_refresh_token

    user = _fake_user()
    family_id = str(uuid.uuid4())
    refresh_token = create_refresh_token(user_id=user.id, family_id=family_id)

    db = MagicMock()

    async def fake_consume_none(_jti):
        # Simulate an already-consumed token.
        return None

    async def fake_revoke_family(_fid):
        pass

    with (
        patch(
            "src.services.auth_service.token_service.consume_refresh_token",
            new=fake_consume_none,
        ),
        patch(
            "src.services.auth_service.token_service.revoke_token_family",
            new=fake_revoke_family,
        ),
        capture_logs() as logs,
    ):
        with pytest.raises(ValueError, match="already used or expired"):
            await auth_service.rotate_refresh_token(db, refresh_token)

    security_events = [e for e in logs if e.get("event") == "auth.token.reuse_detected"]
    assert security_events, (
        "Expected auth.token.reuse_detected to be logged on refresh token reuse, "
        "but no such event was found."
    )
    evt = security_events[0]
    assert evt["outcome"] == "failure"
    assert evt["reason"] == "refresh_reuse"
    assert evt["category"] == "security"
    # actor should be the user id from the JWT sub claim
    assert evt.get("actor") == str(user.id)


@pytest.mark.asyncio
async def test_successful_refresh_emits_token_refreshed_event():
    """A successful refresh token rotation must emit auth.token.refreshed
    with outcome=success.
    """
    from src.auth.jwt import create_refresh_token

    user = _fake_user()
    workspace_id = uuid.uuid4()
    family_id = str(uuid.uuid4())
    refresh_token = create_refresh_token(user_id=user.id, family_id=family_id)

    db = _fake_db_for_rotate(user, workspace_id)

    async def fake_consume(_jti):
        return (user.id, family_id, workspace_id, None)

    async def fake_store(**_kwargs):
        pass

    with (
        patch(
            "src.services.auth_service.token_service.consume_refresh_token",
            new=fake_consume,
        ),
        patch(
            "src.services.auth_service.token_service.store_refresh_token",
            new=fake_store,
        ),
        capture_logs() as logs,
    ):
        result = await auth_service.rotate_refresh_token(db, refresh_token)

    assert "access_token" in result
    security_events = [e for e in logs if e.get("event") == "auth.token.refreshed"]
    assert security_events, (
        "Expected auth.token.refreshed to be logged on successful token rotation, "
        "but no such event was found."
    )
    evt = security_events[0]
    assert evt["outcome"] == "success"
    assert evt["category"] == "security"
    assert evt.get("actor") == str(user.id)


# ---------------------------------------------------------------------------
# Refresh context tracking (per-family ip/UA anomaly signal)
# ---------------------------------------------------------------------------


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value


@pytest.mark.asyncio
async def test_swap_refresh_context_first_seen_and_change():
    from src.services import token_service

    fake = _FakeRedis()
    with patch(
        "src.services.token_service.get_redis", new=AsyncMock(return_value=fake)
    ):
        # First sighting establishes the baseline silently.
        assert await token_service.swap_refresh_context("fam1", "1.1.1.1", "ua") is None
        # Same context — quiet.
        assert await token_service.swap_refresh_context("fam1", "1.1.1.1", "ua") is None
        # Changed ip — previous context returned.
        prev = await token_service.swap_refresh_context("fam1", "2.2.2.2", "ua")
        assert prev == {"ip": "1.1.1.1", "ua": "ua"}
        # Families are independent: another family starts its own baseline.
        assert await token_service.swap_refresh_context("fam2", "9.9.9.9", "ua") is None


@pytest.mark.asyncio
async def test_context_change_writes_activity_row():
    """A refresh from a new ip/UA on the same family must log
    refresh_context_changed with the previous context, and commit it."""
    from src.auth.jwt import create_refresh_token

    user = _fake_user()
    workspace_id = uuid.uuid4()
    family_id = str(uuid.uuid4())
    refresh_token = create_refresh_token(user_id=user.id, family_id=family_id)

    db = _fake_db_for_rotate(user, workspace_id)
    db.commit = AsyncMock()

    async def fake_consume(_jti):
        return (user.id, family_id, workspace_id, None)

    async def fake_store(**_kwargs):
        pass

    with (
        patch(
            "src.services.auth_service.token_service.consume_refresh_token",
            new=fake_consume,
        ),
        patch(
            "src.services.auth_service.token_service.store_refresh_token",
            new=fake_store,
        ),
        patch(
            "src.services.auth_service.token_service.swap_refresh_context",
            new=AsyncMock(return_value={"ip": "1.1.1.1", "ua": "old-ua"}),
        ),
        patch(
            "src.services.activity_service.log_activity", new_callable=AsyncMock
        ) as log_activity,
        capture_logs(),
    ):
        result = await auth_service.rotate_refresh_token(
            db, refresh_token, ip="2.2.2.2", user_agent="new-ua"
        )

    assert "access_token" in result
    kwargs = log_activity.await_args.kwargs
    assert kwargs["action"] == "refresh_context_changed"
    assert kwargs["target_id"] == user.id
    assert kwargs["detail"]["ip"] == "2.2.2.2"
    assert kwargs["detail"]["prev_ip"] == "1.1.1.1"
    assert kwargs["detail"]["prev_user_agent"] == "old-ua"
    assert kwargs["detail"]["family_id"] == family_id
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_context_telemetry_failure_never_revokes_family():
    """The context check sits OUTSIDE the fail-closed rotation block: a Redis
    hiccup must neither 401 the refresh nor revoke the healthy family."""
    from src.auth.jwt import create_refresh_token

    user = _fake_user()
    workspace_id = uuid.uuid4()
    family_id = str(uuid.uuid4())
    refresh_token = create_refresh_token(user_id=user.id, family_id=family_id)

    db = _fake_db_for_rotate(user, workspace_id)

    async def fake_consume(_jti):
        return (user.id, family_id, workspace_id, None)

    async def fake_store(**_kwargs):
        pass

    revoke = AsyncMock()
    with (
        patch(
            "src.services.auth_service.token_service.consume_refresh_token",
            new=fake_consume,
        ),
        patch(
            "src.services.auth_service.token_service.store_refresh_token",
            new=fake_store,
        ),
        patch(
            "src.services.auth_service.token_service.revoke_token_family",
            new=revoke,
        ),
        patch(
            "src.services.auth_service.token_service.swap_refresh_context",
            new=AsyncMock(side_effect=ConnectionError("redis down")),
        ),
        capture_logs(),
    ):
        result = await auth_service.rotate_refresh_token(
            db, refresh_token, ip="2.2.2.2", user_agent="ua"
        )

    assert "access_token" in result
    revoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_reuse_writes_activity_row():
    """Token reuse (theft signal) must land in the admin activity log, not just
    the log stream — and still 401."""
    from src.auth.jwt import create_refresh_token

    user = _fake_user()
    family_id = str(uuid.uuid4())
    refresh_token = create_refresh_token(user_id=user.id, family_id=family_id)

    db = MagicMock()
    db.commit = AsyncMock()

    with (
        patch(
            "src.services.auth_service.token_service.consume_refresh_token",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "src.services.auth_service.token_service.revoke_token_family",
            new=AsyncMock(),
        ),
        patch(
            "src.services.activity_service.log_activity", new_callable=AsyncMock
        ) as log_activity,
        capture_logs(),
    ):
        with pytest.raises(ValueError, match="already used or expired"):
            await auth_service.rotate_refresh_token(
                db, refresh_token, ip="6.6.6.6", user_agent="evil-ua"
            )

    kwargs = log_activity.await_args.kwargs
    assert kwargs["action"] == "refresh_reuse_detected"
    assert kwargs["target_id"] == user.id
    assert kwargs["actor_id"] == user.id
    assert kwargs["detail"]["family_id"] == family_id
    assert kwargs["detail"]["ip"] == "6.6.6.6"
    db.commit.assert_awaited()
