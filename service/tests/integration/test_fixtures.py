# service/tests/integration/test_fixtures.py
"""fixtures.json is internally consistent: tokens decode under its own public key
to the expected claim shapes, and the JWKS carries the signing kid."""

import json
from pathlib import Path

import jwt as pyjwt
import pytest

_FIX = json.loads((Path(__file__).parent / "fixtures" / "fixtures.json").read_text())


def _decode(label: str, aud: str, **opts):
    return pyjwt.decode(
        _FIX["tokens"][label],
        _FIX["public_pem"],
        algorithms=["RS256"],
        audience=aud,
        **opts,
    )


def test_m2m_valid_claim_shape():
    p = _decode("m2m_valid", "duar:m2m")
    assert p["type"] == "m2m"
    assert p["svc"] == "acme-suite"
    assert p["caller"] == "app-a"
    assert p["actions"] == ["*"]
    assert p["aud_target"] is None
    assert "sub" not in p  # honest "no human" token


def test_authz_valid_claim_shape():
    p = _decode("authz_valid", "duar:authz")
    assert p["type"] == "authz"
    assert p["svc"] == "acme-suite"  # svc is the realm slug, not a bare service name


def test_expired_token_is_actually_expired():
    with pytest.raises(pyjwt.ExpiredSignatureError):
        _decode("m2m_expired", "duar:m2m")


def test_wrong_realm_token_has_foreign_svc():
    assert _decode("m2m_wrong_realm", "duar:m2m")["svc"] == "other-realm"


def test_aud_target_token_is_targeted():
    assert _decode("m2m_aud_target", "duar:m2m")["aud_target"] == "billing"


def test_jwks_contains_the_signing_kid():
    kid = pyjwt.get_unverified_header(_FIX["tokens"]["m2m_valid"])["kid"]
    assert any(k["kid"] == kid for k in _FIX["jwks"]["keys"])
