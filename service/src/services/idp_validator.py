"""IdP token validator — validates tokens from external identity providers.

Supports:
- Google OIDC (JWT with JWKS verification)
- EntraID OIDC (JWT with JWKS verification)
- GitHub OAuth (opaque token validated via API calls)
"""

from __future__ import annotations

import base64
import os
import time
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from jwt.algorithms import RSAAlgorithm

from src.config import settings
from src.services.auth_service import extract_email_claim, is_email_verified_claim

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class IdpValidationError(Exception):
    """Raised when an IdP token fails validation."""


# ---------------------------------------------------------------------------
# Provider configuration (OIDC only — GitHub is handled separately)
# ---------------------------------------------------------------------------

_PROVIDER_CONFIG: dict[str, dict[str, Any]] = {
    "google": {
        "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
        "issuer": "https://accounts.google.com",
        "audience": lambda: settings.google_client_id,
    },
    "entra_id": {
        "jwks_uri": lambda: (
            f"https://login.microsoftonline.com/{settings.entra_tenant_id}"
            f"/discovery/v2.0/keys"
        ),
        "issuer": lambda: (
            f"https://login.microsoftonline.com/{settings.entra_tenant_id}/v2.0"
        ),
        "audience": lambda: settings.entra_client_id,
    },
}


def _load_pem_pubkey(raw: str):
    """Load an RSA public key from raw PEM or base64-encoded PEM (rig env convenience)."""
    pem = raw if "BEGIN" in raw else base64.b64decode(raw).decode()
    return serialization.load_pem_public_key(pem.encode())


def _effective_audience(config: dict[str, Any], expected_audiences: list[str] | None):
    """The audience(s) the token must match. When the calling app supplies its registered
    IdP audience(s) (per-app binding), validate against those; otherwise fall back to the
    provider's single deployment-wide configured audience (preserves prior behavior)."""
    if expected_audiences:
        return list(expected_audiences)
    aud = config["audience"]
    return aud() if callable(aud) else aud


def _register_test_provider() -> None:
    """Register a gated, static-key 'test_oidc' enrichment provider when the rig env var
    ``TEST_TRUSTED_ISSUER_PUBKEY`` is set. UNSET (prod default) => not registered =>
    /authz/resolve returns 'Unsupported provider' (fail closed). Validates exactly like a real
    OIDC provider (signature + issuer + audience + email_verified + nonce) but against a
    statically-configured public key instead of a fetched JWKS — the seam used by the Layer-2
    trust-boundary pentest to present validly-signed-but-malicious-claim tokens.

    Read from os.environ (not Settings) on purpose: a test-only hook must NOT enter the prod
    config surface (e.g. /admin/system/settings). Mirrors the existing out-of-band
    ``_override_key`` test hook. Pubkey may be raw PEM or base64-encoded PEM."""
    raw = os.getenv("TEST_TRUSTED_ISSUER_PUBKEY", "").strip()
    if not raw:
        _PROVIDER_CONFIG.pop("test_oidc", None)
        return
    _PROVIDER_CONFIG["test_oidc"] = {
        "static_pubkey": raw,
        "issuer": os.getenv("TEST_TRUSTED_ISSUER", ""),
        "audience": os.getenv("TEST_TRUSTED_AUDIENCE", ""),
    }


_register_test_provider()

# ---------------------------------------------------------------------------
# JWKS cache (with TTL)
# ---------------------------------------------------------------------------

_JWKS_CACHE_TTL = 3600  # 1 hour — Google rotates keys roughly every 6 hours
# Floor for forced (decode-failure) refetches — bounds the amplification a
# forged token could otherwise get by triggering a JWKS fetch per attempt.
_JWKS_REFRESH_MIN_INTERVAL = 60

_jwks_cache: dict[str, tuple[list[dict], float]] = {}


async def _fetch_jwks(provider: str, *, force_refresh: bool = False) -> list[dict]:
    """Fetch and cache JWKS public keys for the given OIDC provider."""
    cached = _jwks_cache.get(provider)
    if cached:
        keys, fetched_at = cached
        age = time.monotonic() - fetched_at
        if age < (_JWKS_REFRESH_MIN_INTERVAL if force_refresh else _JWKS_CACHE_TTL):
            return keys

    config = _PROVIDER_CONFIG[provider]
    jwks_uri = config["jwks_uri"]
    if callable(jwks_uri):
        jwks_uri = jwks_uri()

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(jwks_uri)
        resp.raise_for_status()
        keys = resp.json()["keys"]

    _jwks_cache[provider] = (keys, time.monotonic())
    return keys


# ---------------------------------------------------------------------------
# OIDC token validation (Google / EntraID)
# ---------------------------------------------------------------------------


def _decode_with_jwks(
    idp_token: str, jwks: list[dict], audience: Any, issuer: str
) -> tuple[dict[str, Any] | None, Exception | None]:
    """Try each JWKS key; return (payload, None) or (None, last_error)."""
    last_error: Exception | None = None
    for key_data in jwks:
        public_key = RSAAlgorithm.from_jwk(key_data)
        try:
            payload = jwt.decode(
                idp_token,
                public_key,
                algorithms=["RS256"],
                audience=audience,
                issuer=issuer,
            )
            return payload, None
        except jwt.ExpiredSignatureError:
            # Don't try other keys — the token is definitively expired
            raise IdpValidationError("Token expired")
        except jwt.PyJWTError as exc:
            last_error = exc
            continue
    return None, last_error


async def _validate_oidc_token(
    idp_token: str,
    provider: str,
    *,
    expected_nonce: str | None = None,
    expected_audiences: list[str] | None = None,
    _override_key: Any | None = None,
) -> dict[str, Any]:
    """Validate an OIDC JWT and return normalised claims."""
    config = _PROVIDER_CONFIG[provider]
    static_pubkey = config.get("static_pubkey")

    if static_pubkey is not None:
        # Gated test provider — verify the signature against a statically-configured public
        # key, but enforce issuer + audience (and the shared email_verified / nonce checks
        # below) exactly like a real OIDC provider.
        audience = _effective_audience(config, expected_audiences)
        issuer = config["issuer"]() if callable(config["issuer"]) else config["issuer"]
        try:
            payload = jwt.decode(
                idp_token,
                _load_pem_pubkey(static_pubkey),
                algorithms=["RS256"],
                audience=audience,
                issuer=issuer,
            )
        except jwt.ExpiredSignatureError:
            raise IdpValidationError("Token expired")
        except jwt.PyJWTError as exc:
            raise IdpValidationError(f"Invalid token: {exc}")
    elif _override_key is not None:
        # Test mode — skip audience/issuer verification, use supplied key
        try:
            payload = jwt.decode(
                idp_token,
                _override_key,
                algorithms=["RS256"],
                options={
                    "verify_aud": False,
                    "verify_iss": False,
                },
            )
        except jwt.ExpiredSignatureError:
            raise IdpValidationError("Token expired")
        except jwt.PyJWTError as exc:
            raise IdpValidationError(f"Invalid token: {exc}")
    else:
        audience = _effective_audience(config, expected_audiences)
        issuer = config["issuer"]
        if callable(issuer):
            issuer = issuer()

        jwks = await _fetch_jwks(provider)
        payload, last_error = _decode_with_jwks(idp_token, jwks, audience, issuer)
        if payload is None:
            # The signing key may have rotated in after our cached snapshot
            # (≤1h old) — refetch once, rate-limited, and retry before
            # rejecting. Mirrors PyJWKClient's refresh-on-miss in the SDK.
            fresh = await _fetch_jwks(provider, force_refresh=True)
            if fresh is not jwks:
                payload, last_error = _decode_with_jwks(
                    idp_token, fresh, audience, issuer
                )

        if payload is None:
            raise IdpValidationError(
                f"Invalid token: {last_error}" if last_error else "Invalid token"
            )

    # An address-less token can't be org-gated or provisioned. Say so precisely —
    # bare KeyError'ing on payload["email"] below surfaced as a 500, and Entra
    # omits `email` unless the app registration adds it as an optional claim.
    email = extract_email_claim(payload)
    if not email:
        raise IdpValidationError(
            "IdP token carries no email address — add the 'email' optional claim "
            "to the application registration (Entra) or request the 'email' scope"
        )

    # Require verified email — strict True (rejects stringified "true"/"false" from buggy IdPs)
    if not is_email_verified_claim(payload, provider):
        raise IdpValidationError("Email not verified")

    # Replay protection: if caller supplied a nonce, require the IdP token to carry it.
    if expected_nonce is not None and payload.get("nonce") != expected_nonce:
        raise IdpValidationError("Nonce mismatch")

    return {
        "sub": payload["sub"],
        "email": email,
        "name": payload.get("name", ""),
        # No `email_verified` key on purpose: reaching this line already means the
        # gate above passed, so echoing a hardcoded True would be a footgun — a
        # future caller could read it as "the IdP asserted this", which is exactly
        # what Entra does NOT do. Verification is a gate here, never a payload.
        "picture": payload.get("picture"),
    }


# ---------------------------------------------------------------------------
# GitHub token validation (opaque OAuth token → API calls)
# ---------------------------------------------------------------------------


async def _validate_github_token(idp_token: str) -> dict[str, Any]:
    """Validate a GitHub OAuth token via the GitHub API."""
    # Fail closed if GitHub IdP isn't configured for this deployment — without
    # client credentials we cannot verify the token was issued to Duar's
    # OAuth app, so we cannot trust the token at all.
    if not settings.github_client_id or not settings.github_client_secret:
        raise IdpValidationError("GitHub IdP not configured on this deployment")

    headers = {
        "Authorization": f"Bearer {idp_token}",
        "Accept": "application/vnd.github+json",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        # App-binding check: GitHub access tokens are opaque and `/user`
        # authenticates the underlying user regardless of which OAuth app
        # holds the token. Without this step, any token from an
        # attacker-registered OAuth app that the victim consented to can be
        # replayed to impersonate the victim against Duar. The
        # `/applications/{client_id}/token` endpoint authenticates as the
        # OAuth app (HTTP Basic with client_id:client_secret) and returns 200
        # only when the submitted token was issued to *that* app; 404
        # otherwise. This is the OIDC `aud` equivalent for opaque tokens.
        binding_resp = await client.post(
            f"https://api.github.com/applications/{settings.github_client_id}/token",
            auth=(settings.github_client_id, settings.github_client_secret),
            headers={"Accept": "application/vnd.github+json"},
            json={"access_token": idp_token},
        )
        if binding_resp.status_code != 200:
            raise IdpValidationError("GitHub token was not issued to this application")

        # Fetch user profile
        profile_resp = await client.get("https://api.github.com/user", headers=headers)
        if profile_resp.status_code != 200:
            raise IdpValidationError("Invalid GitHub token")
        profile = profile_resp.json()

        # Fetch user emails
        emails_resp = await client.get(
            "https://api.github.com/user/emails", headers=headers
        )
        if emails_resp.status_code != 200:
            raise IdpValidationError("Could not fetch GitHub emails")
        emails = emails_resp.json()

    # Find the primary verified email
    primary_email = None
    for entry in emails:
        if entry.get("primary") and entry.get("verified"):
            primary_email = entry["email"]
            break

    if primary_email is None:
        raise IdpValidationError("Email not verified")

    return {
        "sub": f"github|{profile['id']}",
        "email": primary_email,
        "name": profile.get("name") or profile.get("login", ""),
        "picture": profile.get("avatar_url"),
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def validate_idp_token(
    idp_token: str,
    provider: str,
    *,
    expected_nonce: str | None = None,
    expected_audiences: list[str] | None = None,
    _override_key: Any | None = None,
) -> dict[str, Any]:
    """Validate an IdP token and return normalised user claims.

    Parameters
    ----------
    idp_token:
        The raw token string (JWT for OIDC providers, opaque for GitHub).
    provider:
        One of ``"google"``, ``"entra_id"``, ``"github"``.
    expected_nonce:
        If provided (OIDC providers only), require the token's ``nonce``
        claim to match this value. Ignored for GitHub (opaque token).
    expected_audiences:
        If provided (OIDC providers only), require the token's ``aud`` to match
        one of these — the calling app's registered IdP client_id(s). Binds the
        token to the app it was issued for, so a token minted for one app cannot
        mint via another app's service key. Unset => fall back to the provider's
        single deployment-wide audience (prior behavior). GitHub tokens are
        opaque and cannot be audience-bound — if this is set, GitHub is
        rejected outright (fail closed, never silently unbound).
    _override_key:
        **Test hook** — when provided, uses this key instead of fetching JWKS
        and skips audience/issuer verification.

    Returns
    -------
    dict with keys: ``sub``, ``email``, ``name``, ``picture``. Email verification is
    a gate (raises below), never a field on the result.

    Raises
    ------
    IdpValidationError
        If the token is invalid, expired, the email is not verified, the nonce
        claim does not match ``expected_nonce``, or the audience is not one of
        ``expected_audiences``.
    """
    if provider in _PROVIDER_CONFIG:
        return await _validate_oidc_token(
            idp_token,
            provider,
            expected_nonce=expected_nonce,
            expected_audiences=expected_audiences,
            _override_key=_override_key,
        )
    elif provider == "github":
        if expected_audiences:
            # GitHub tokens are opaque — per-app audience binding cannot be
            # enforced (only the deployment-wide app-binding check below).
            # Fail closed rather than silently skip a configured control.
            raise IdpValidationError(
                "Per-app IdP audience binding is configured for this app but "
                "cannot be enforced for GitHub tokens — remove GitHub from the "
                "app's providers or clear allowed_idp_audiences"
            )
        return await _validate_github_token(idp_token)
    else:
        raise IdpValidationError(f"Unsupported provider: {provider}")
