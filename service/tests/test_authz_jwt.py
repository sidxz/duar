"""Tests for authz token creation and decoding."""

import uuid

import jwt as pyjwt
import pytest

from src.auth.jwt import _AUD_AUTHZ, create_authz_token, decode_token


class TestAuthzToken:
    def test_create_and_decode(self):
        user_id = uuid.uuid4()
        workspace_id = uuid.uuid4()
        token = create_authz_token(
            user_id=user_id,
            idp_sub="google|12345",
            workspace_id=workspace_id,
            workspace_slug="acme-corp",
            workspace_role="editor",
            actions=["read", "write"],
            service_name="notes",
            org_id=None,
            org_slug=None,
            org_is_public=False,
        )
        payload = decode_token(token, audience=_AUD_AUTHZ)
        assert payload["sub"] == str(user_id)
        assert payload["idp_sub"] == "google|12345"
        assert payload["svc"] == "notes"
        assert payload["wid"] == str(workspace_id)
        assert payload["wrole"] == "editor"
        assert payload["actions"] == ["read", "write"]
        assert payload["aud"] == "duar:authz"
        assert payload["type"] == "authz"

    def test_wrong_audience_rejected(self):
        token = create_authz_token(
            user_id=uuid.uuid4(),
            idp_sub="google|12345",
            workspace_id=uuid.uuid4(),
            workspace_slug="test",
            workspace_role="viewer",
            actions=[],
            service_name="test-svc",
            org_id=None,
            org_slug=None,
            org_is_public=False,
        )
        with pytest.raises(pyjwt.InvalidAudienceError):
            decode_token(token, audience="duar:access")
