import uuid
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from src.config import Settings


def _write_keypair(tmp_path, name):
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
    priv_path = tmp_path / f"{name}_priv.pem"
    pub_path = tmp_path / f"{name}_pub.pem"
    priv_path.write_bytes(priv)
    pub_path.write_bytes(pub)
    return priv_path, pub_path


@pytest.fixture
def two_keys(tmp_path, monkeypatch):
    from src.auth import key_provider
    from src.config import settings

    cur_priv, cur_pub = _write_keypair(tmp_path, "cur")
    _old_priv, old_pub = _write_keypair(tmp_path, "old")
    monkeypatch.setattr(settings, "jwt_private_key_path", cur_priv)
    monkeypatch.setattr(settings, "jwt_public_key_path", cur_pub)
    monkeypatch.setattr(settings, "jwt_previous_public_key_paths", str(old_pub))
    key_provider.reset_cache()
    yield {"cur_pub": cur_pub.read_text(), "old_pub": old_pub.read_text()}
    key_provider.reset_cache()


def test_signing_key_returns_private_and_kid(two_keys):
    from src.auth import key_provider

    private_pem, kid = key_provider.signing_key()
    assert "PRIVATE KEY" in private_pem
    assert kid == key_provider.thumbprint_kid(two_keys["cur_pub"])


def test_verification_keys_include_current_and_previous(two_keys):
    from src.auth import key_provider

    keys = key_provider.verification_keys()
    cur_kid = key_provider.thumbprint_kid(two_keys["cur_pub"])
    old_kid = key_provider.thumbprint_kid(two_keys["old_pub"])
    assert set(keys) == {cur_kid, old_kid}
    assert keys[cur_kid] == two_keys["cur_pub"]


def _make_authz(**overrides):
    from src.auth.jwt import create_authz_token

    kwargs = dict(
        user_id=uuid.uuid4(),
        idp_sub="google|1",
        workspace_id=uuid.uuid4(),
        workspace_slug="w",
        workspace_role="viewer",
        actions=[],
        service_name="svc",
        org_id=None,
        org_slug=None,
        org_is_public=False,
    )
    kwargs.update(overrides)
    return create_authz_token(**kwargs)


def test_issued_token_has_kid(two_keys):
    from src.auth import key_provider

    token = _make_authz()
    header = pyjwt.get_unverified_header(token)
    _, expected_kid = key_provider.signing_key()
    assert header["kid"] == expected_kid


def test_decode_rejects_token_without_kid(two_keys):
    from src.auth import key_provider
    from src.auth.jwt import _AUD_AUTHZ, decode_token
    from src.config import settings

    private_pem, _ = key_provider.signing_key()
    payload = {
        "iss": settings.base_url,
        "sub": str(uuid.uuid4()),
        "aud": _AUD_AUTHZ,
        "type": "authz",
        "exp": datetime.now(UTC) + timedelta(minutes=5),
    }
    no_kid = pyjwt.encode(payload, private_pem, algorithm="RS256")
    with pytest.raises(pyjwt.InvalidTokenError):
        decode_token(no_kid, audience=_AUD_AUTHZ)


def test_decode_rejects_unknown_kid(two_keys):
    from src.auth import key_provider
    from src.auth.jwt import _AUD_AUTHZ, decode_token

    token = _make_authz()
    saved = key_provider._verification_cache
    key_provider._verification_cache = {}
    try:
        with pytest.raises(pyjwt.InvalidTokenError):
            decode_token(token, audience=_AUD_AUTHZ)
    finally:
        key_provider._verification_cache = saved


def test_rotation_continuity(two_keys, tmp_path, monkeypatch):
    """A token signed by the OLD key still verifies while that key is in the
    verify set, then fails once it is dropped."""
    from src.auth import key_provider
    from src.auth.jwt import _AUD_AUTHZ, decode_token
    from src.config import settings

    old_pub_pem = two_keys["cur_pub"]
    token_from_old = _make_authz()

    # Rotate: brand-new current key; the just-used key becomes verify-only.
    new_priv, new_pub = _write_keypair(tmp_path, "new")
    old_pub_path = tmp_path / "rotated_old_pub.pem"
    old_pub_path.write_text(old_pub_pem)
    monkeypatch.setattr(settings, "jwt_private_key_path", new_priv)
    monkeypatch.setattr(settings, "jwt_public_key_path", new_pub)
    monkeypatch.setattr(settings, "jwt_previous_public_key_paths", str(old_pub_path))
    key_provider.reset_cache()

    assert decode_token(token_from_old, audience=_AUD_AUTHZ)["svc"] == "svc"
    new_token = _make_authz()
    assert decode_token(new_token, audience=_AUD_AUTHZ)["svc"] == "svc"

    # Drop the old key from the verify set → old token now rejected.
    monkeypatch.setattr(settings, "jwt_previous_public_key_paths", "")
    key_provider.reset_cache()
    with pytest.raises(pyjwt.InvalidTokenError):
        decode_token(token_from_old, audience=_AUD_AUTHZ)
    assert decode_token(new_token, audience=_AUD_AUTHZ)["svc"] == "svc"


def test_jwks_publishes_all_verification_keys(two_keys):
    from src.auth import jwks, key_provider

    jwks._jwks_cache = None  # bypass TTL cache
    out = jwks.build_jwks()
    published = {k["kid"] for k in out["keys"]}
    assert published == set(key_provider.verification_keys())
    for k in out["keys"]:
        assert k["use"] == "sig" and k["alg"] == "RS256" and k["kty"] == "RSA"
    jwks._jwks_cache = None


def test_previous_public_key_paths_parses_csv():
    s = Settings(jwt_previous_public_key_paths="keys/old1.pem, keys/old2.pem")
    assert s.jwt_previous_public_key_paths_list == ["keys/old1.pem", "keys/old2.pem"]


def test_previous_public_key_paths_empty_default():
    s = Settings()
    assert s.jwt_previous_public_key_paths_list == []
