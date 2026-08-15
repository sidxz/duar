"""The no-user realm m2m token: service identity only, duar:m2m audience."""

import pytest

from src.auth.jwt import _AUD_ACCESS, _AUD_M2M, create_m2m_token, decode_token


def test_m2m_token_carries_service_identity_no_user():
    token = create_m2m_token(svc="acme-suite", caller="docs", ttl_s=300)
    payload = decode_token(token, audience=_AUD_M2M)
    assert payload["type"] == "m2m"
    assert payload["svc"] == "acme-suite"
    assert payload["caller"] == "docs"
    assert payload["actions"] == ["*"]
    assert payload["aud_target"] is None
    # An honest "no human" token: zero user/identity claims.
    assert "sub" not in payload
    assert "email" not in payload
    assert "idp_sub" not in payload


def test_m2m_token_exp_honors_ttl():
    token = create_m2m_token(svc="acme-suite", caller="docs", ttl_s=120)
    payload = decode_token(token, audience=_AUD_M2M)
    assert payload["exp"] - payload["iat"] == 120


def test_m2m_token_audience_is_distinct_from_access():
    """Audience separation defends against token-type confusion: a no-user m2m
    token must NOT validate as a user access token."""
    token = create_m2m_token(svc="acme-suite", caller="docs", ttl_s=300)
    with pytest.raises(Exception):
        decode_token(token, audience=_AUD_ACCESS)
