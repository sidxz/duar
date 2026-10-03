"""Org claims (oid/oslug/opub) on access + authz tokens."""

import uuid

from src.auth.jwt import (
    _AUD_ACCESS,
    _AUD_AUTHZ,
    create_access_token,
    create_authz_token,
    decode_token,
)


def test_access_token_carries_org_claims():
    org_id = uuid.uuid4()
    token = create_access_token(
        user_id=uuid.uuid4(),
        email="alice@tamu.edu",
        name="Alice",
        workspace_id=uuid.uuid4(),
        workspace_slug="acme",
        workspace_role="editor",
        groups=[],
        org_id=str(org_id),
        org_slug="tamu",
        org_is_public=False,
    )
    payload = decode_token(token, audience=_AUD_ACCESS)
    assert payload["oid"] == str(org_id)
    assert payload["oslug"] == "tamu"
    assert payload["opub"] is False


def test_access_token_public_org_flag():
    token = create_access_token(
        user_id=uuid.uuid4(),
        email="bob@gmail.com",
        name="Bob",
        workspace_id=uuid.uuid4(),
        workspace_slug="acme",
        workspace_role="viewer",
        groups=[],
        org_id=str(uuid.uuid4()),
        org_slug="public",
        org_is_public=True,
    )
    payload = decode_token(token, audience=_AUD_ACCESS)
    assert payload["opub"] is True


def test_authz_token_carries_org_claims():
    org_id = uuid.uuid4()
    token = create_authz_token(
        user_id=uuid.uuid4(),
        idp_sub="google|123",
        workspace_id=uuid.uuid4(),
        workspace_slug="acme",
        workspace_role="editor",
        actions=["read"],
        service_name="notes",
        org_id=str(org_id),
        org_slug="tamu",
        org_is_public=False,
    )
    payload = decode_token(token, audience=_AUD_AUTHZ)
    assert payload["oid"] == str(org_id)
    assert payload["oslug"] == "tamu"
    assert payload["opub"] is False


def test_access_token_org_claims_are_null_not_omitted():
    # When there is no org, the keys must still be present (null/false) so SDK
    # consumers never have to guard against a missing key.
    token = create_access_token(
        user_id=uuid.uuid4(),
        email="x@x.com",
        name="X",
        workspace_id=uuid.uuid4(),
        workspace_slug="ws",
        workspace_role="viewer",
        groups=[],
        org_id=None,
        org_slug=None,
        org_is_public=False,
    )
    payload = decode_token(token, audience=_AUD_ACCESS)
    assert "oid" in payload and payload["oid"] is None
    assert "oslug" in payload and payload["oslug"] is None
    assert "opub" in payload and payload["opub"] is False


def test_authz_token_org_claims_are_null_not_omitted():
    token = create_authz_token(
        user_id=uuid.uuid4(),
        idp_sub="google|123",
        workspace_id=uuid.uuid4(),
        workspace_slug="ws",
        workspace_role="viewer",
        actions=[],
        service_name="notes",
        org_id=None,
        org_slug=None,
        org_is_public=False,
    )
    payload = decode_token(token, audience=_AUD_AUTHZ)
    assert "oid" in payload and payload["oid"] is None
    assert "oslug" in payload and payload["oslug"] is None
    assert "opub" in payload and payload["opub"] is False
