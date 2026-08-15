"""JWKS (JSON Web Key Set) builder for Duar's RSA signing keys."""

import json
import time

from cryptography.hazmat.primitives.serialization import load_pem_public_key
from jwt.algorithms import RSAAlgorithm

from src.auth import key_provider

_jwks_cache: dict | None = None
_jwks_cache_time: float = 0
_JWKS_CACHE_TTL = 3600  # 1 hour — rebuild after key rotation + restart


def build_jwks() -> dict:
    """Build a JWKS response from all current + retired verification keys.

    Each key is published with its RFC 7638 thumbprint ``kid`` so clients can
    select the right key during a rotation. Result is cached with a TTL.
    """
    global _jwks_cache, _jwks_cache_time
    if (
        _jwks_cache is not None
        and (time.monotonic() - _jwks_cache_time) < _JWKS_CACHE_TTL
    ):
        return _jwks_cache

    keys = []
    for kid, public_pem in key_provider.verification_keys().items():
        pub_key = load_pem_public_key(public_pem.encode())
        jwk = json.loads(RSAAlgorithm.to_jwk(pub_key))
        jwk["use"] = "sig"
        jwk["alg"] = "RS256"
        jwk["kid"] = kid
        keys.append(jwk)

    _jwks_cache = {"keys": keys}
    _jwks_cache_time = time.monotonic()
    return _jwks_cache
