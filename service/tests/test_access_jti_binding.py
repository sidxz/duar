"""Regression test: refresh-family revocation must actually blacklist
the paired access tokens.

Vulnerability: ``token_service.store_refresh_token`` accepts an optional
``access_jti`` parameter specifically so ``revoke_token_family`` can blacklist
the paired access token's jti when reuse is detected. But ``auth_service``'s
token issuance paths never passed it, so the blacklist loop was dead code.
This test pins the fix by intercepting the Redis-backed store call and
asserting the access token's jti is forwarded.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.auth.jwt import _AUD_ACCESS, decode_token
from src.services import auth_service


def _fake_user() -> MagicMock:
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "test@example.com"
    user.name = "Test User"
    user.is_active = True
    user.is_admin = False
    # No org — avoids the db.get(Organization, ...) branch.
    user.organization_id = None
    return user


def _fake_db_for_issue(membership_role: str = "editor") -> MagicMock:
    """Mock DB that lets ``issue_tokens`` progress past its queries.

    Queues: membership check, workspace_allows_org (open = empty scalars), groups.
    """
    db = MagicMock()
    membership = MagicMock()
    membership.role = membership_role

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


@pytest.mark.asyncio
async def test_issue_tokens_forwards_access_jti_to_store():
    """issue_tokens must pass the minted access token's jti to
    ``store_refresh_token`` so family revocation can blacklist it.
    """
    user = _fake_user()
    workspace_id = uuid.uuid4()
    db = _fake_db_for_issue()

    captured: dict = {}

    async def fake_store(**kwargs):
        captured.update(kwargs)

    with patch(
        "src.services.auth_service.token_service.store_refresh_token",
        new=fake_store,
    ):
        result = await auth_service.issue_tokens(
            db=db,
            user=user,
            workspace_id=workspace_id,
            workspace_slug="test-ws",
        )

    # Decode the minted access token to pull its jti
    access_payload = decode_token(result["access_token"], audience=_AUD_ACCESS)
    minted_access_jti = access_payload["jti"]

    assert captured.get("access_jti"), (
        "issue_tokens did not forward access_jti to store_refresh_token; "
        "revoke_token_family will be unable to blacklist the paired access "
        "token on reuse detection."
    )
    assert captured["access_jti"] == minted_access_jti, (
        "access_jti passed to store_refresh_token must equal the access "
        "token's jti so revocation targets the correct token."
    )


@pytest.mark.asyncio
async def test_rotate_refresh_token_forwards_access_jti_to_store():
    """rotate_refresh_token must also forward the new access token's jti."""
    user = _fake_user()
    workspace_id = uuid.uuid4()
    family_id = str(uuid.uuid4())

    # Mock DB: user lookup + workspace membership + workspace + groups.
    # user.organization_id is None (set in _fake_user) so db.get(Organization)
    # is skipped; only User and Workspace are fetched.
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

    # Build a real refresh token so rotate can decode it
    from src.auth.jwt import create_refresh_token

    refresh_token = create_refresh_token(user_id=user.id, family_id=family_id)

    captured: dict = {}

    async def fake_store(**kwargs):
        captured.update(kwargs)

    async def fake_consume(jti):
        # Simulate a successful first-use consumption.
        return (user.id, family_id, workspace_id, None)

    with (
        patch(
            "src.services.auth_service.token_service.store_refresh_token",
            new=fake_store,
        ),
        patch(
            "src.services.auth_service.token_service.consume_refresh_token",
            new=fake_consume,
        ),
    ):
        result = await auth_service.rotate_refresh_token(db, refresh_token)

    access_payload = decode_token(result["access_token"], audience=_AUD_ACCESS)
    minted_access_jti = access_payload["jti"]

    assert captured.get("access_jti"), (
        "rotate_refresh_token did not forward access_jti to store_refresh_token; "
        "the blacklist loop in revoke_token_family remains dead code after rotation."
    )
    assert captured["access_jti"] == minted_access_jti
