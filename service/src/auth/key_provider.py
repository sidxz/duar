"""Signing/verification key provider.

The single seam between key *material* and the rest of the auth layer. Today it
reads PEM files from disk; a future KMS/HSM implementation can replace these
functions without touching token, JWKS, or verify logic.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives.serialization import load_pem_public_key
from jwt.algorithms import RSAAlgorithm

from src.config import settings

_signing_cache: tuple[str, str] | None = None
_verification_cache: dict[str, str] | None = None


def thumbprint_kid(public_pem: str) -> str:
    """RFC 7638 JWK thumbprint of an RSA public key, used as its kid."""
    pub = load_pem_public_key(public_pem.encode())
    jwk = json.loads(RSAAlgorithm.to_jwk(pub))
    thumbprint_input = json.dumps(
        {"e": jwk["e"], "kty": "RSA", "n": jwk["n"]},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return (
        base64.urlsafe_b64encode(hashlib.sha256(thumbprint_input).digest())
        .rstrip(b"=")
        .decode("ascii")
    )


def signing_key() -> tuple[str, str]:
    """Return (private_pem, kid) for the current signing key."""
    global _signing_cache
    if _signing_cache is None:
        private_pem = settings.jwt_private_key_path.read_text()
        public_pem = settings.jwt_public_key_path.read_text()
        _signing_cache = (private_pem, thumbprint_kid(public_pem))
    return _signing_cache


def verification_keys() -> dict[str, str]:
    """Return {kid: public_pem} for the current key plus any retired keys."""
    global _verification_cache
    if _verification_cache is None:
        keys: dict[str, str] = {}
        current_pub = settings.jwt_public_key_path.read_text()
        keys[thumbprint_kid(current_pub)] = current_pub
        for path in settings.jwt_previous_public_key_paths_list:
            pem = Path(path).read_text()
            keys[thumbprint_kid(pem)] = pem
        _verification_cache = keys
    return _verification_cache


def reset_cache() -> None:
    """Clear cached key material (after rotation/reload and in tests)."""
    global _signing_cache, _verification_cache
    _signing_cache = None
    _verification_cache = None
