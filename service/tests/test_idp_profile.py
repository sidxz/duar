"""_idp_profile: one profile extractor for every browser callback (proxy, admin, onboard)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.api.auth_routes import IdpProfileError, _error_page, _idp_profile


def _github_client(emails):
    client = SimpleNamespace()
    client.get = AsyncMock(
        side_effect=[
            SimpleNamespace(
                json=lambda: {
                    "id": 42,
                    "name": "Octo",
                    "login": "octo",
                    "avatar_url": "https://a/x.png",
                }
            ),
            SimpleNamespace(json=lambda: emails),
        ]
    )
    return client


@pytest.mark.asyncio
async def test_github_uses_primary_verified_email():
    prof = await _idp_profile(
        _github_client([{"email": "o@example.com", "primary": True, "verified": True}]),
        token={"access_token": "t"},
        provider="github",
    )
    assert prof.provider_user_id == "42"
    assert prof.email == "o@example.com"
    assert prof.name == "Octo"
    assert prof.avatar_url == "https://a/x.png"
    assert prof.provider_data["email"] == "o@example.com"


@pytest.mark.asyncio
async def test_github_unverified_primary_rejected():
    with pytest.raises(IdpProfileError) as ei:
        await _idp_profile(
            _github_client(
                [{"email": "o@example.com", "primary": True, "verified": False}]
            ),
            token={},
            provider="github",
        )
    assert ei.value.reason == "email_not_verified"
    assert ei.value.count_for_stuffing is True


@pytest.mark.asyncio
async def test_oidc_verified():
    token = {
        "userinfo": {
            "sub": "s1",
            "email": "g@example.com",
            "email_verified": True,
            "name": "G",
            "picture": "https://p",
        }
    }
    prof = await _idp_profile(SimpleNamespace(), token=token, provider="google")
    assert (prof.provider_user_id, prof.email, prof.name, prof.avatar_url) == (
        "s1",
        "g@example.com",
        "G",
        "https://p",
    )
    assert prof.provider_data == token["userinfo"]


@pytest.mark.asyncio
async def test_oidc_unverified_rejected():
    token = {
        "userinfo": {"sub": "s1", "email": "g@example.com", "email_verified": "true"}
    }
    with pytest.raises(IdpProfileError) as ei:
        await _idp_profile(SimpleNamespace(), token=token, provider="google")
    assert ei.value.reason == "email_not_verified"


@pytest.mark.asyncio
async def test_oidc_no_email_is_not_counted_for_stuffing(monkeypatch):
    from src.config import settings

    monkeypatch.setattr(settings, "entra_tenant_id", "tid")
    token = {"userinfo": {"sub": "s1", "tid": "tid"}}
    with pytest.raises(IdpProfileError) as ei:
        await _idp_profile(SimpleNamespace(), token=token, provider="entra_id")
    assert ei.value.reason == "no_email_claim"
    assert ei.value.count_for_stuffing is False


def test_error_page_back_link_is_escaped_and_optional():
    plain = _error_page(400, "T", "M").body.decode()
    assert "Back to" not in plain
    linked = _error_page(400, "T", "M", back_href='https://x/"><s>').body.decode()
    assert 'href="https://x/&#34;&gt;&lt;s&gt;"' in linked  # Jinja autoescape
    assert "Back to sign-in" in linked
