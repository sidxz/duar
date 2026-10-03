"""Shared fixtures for SDK tests."""

import datetime
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture(scope="session")
def rsa_keypair():
    """Generate an RSA keypair for signing/verifying JWTs."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


@pytest.fixture()
def user_id():
    return uuid.uuid4()


@pytest.fixture()
def workspace_id():
    return uuid.uuid4()


@pytest.fixture()
def jwt_payload(user_id, workspace_id):
    """Standard JWT payload matching the middleware's expected claims."""
    return {
        "sub": str(user_id),
        "email": "alice@example.com",
        "name": "Alice",
        "wid": str(workspace_id),
        "wslug": "acme-corp",
        "wrole": "editor",
        "groups": [],
        "aud": "duar:access",
        "exp": datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1),
        "iat": datetime.datetime.now(datetime.UTC),
    }


@pytest.fixture()
def make_token(rsa_keypair):
    """Factory to encode a JWT payload with the test private key."""
    private_pem, _ = rsa_keypair

    def _make(payload: dict) -> str:
        return jwt.encode(payload, private_pem, algorithm="RS256")

    return _make


@pytest.fixture()
def valid_token(jwt_payload, make_token):
    return make_token(jwt_payload)
