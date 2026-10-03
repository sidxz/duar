import uuid
from datetime import UTC, datetime, timedelta

import jwt

from src.auth import key_provider
from src.config import settings


def _sign(payload: dict) -> str:
    private_pem, kid = key_provider.signing_key()
    return jwt.encode(
        payload, private_pem, algorithm=settings.jwt_algorithm, headers={"kid": kid}
    )


def get_public_key() -> str:
    """Current signing key's public PEM (kept for back-compat)."""
    _, kid = key_provider.signing_key()
    return key_provider.verification_keys()[kid]


_AUD_ACCESS = "duar:access"
_AUD_ADMIN = "duar:admin"
_AUD_REFRESH = "duar:refresh"
_AUD_AUTHZ = "duar:authz"
_AUD_M2M = "duar:m2m"
_ISSUER = settings.base_url


def create_access_token(
    user_id: uuid.UUID,
    email: str,
    name: str,
    workspace_id: uuid.UUID,
    workspace_slug: str,
    workspace_role: str,
    groups: list[uuid.UUID],
    org_id: str | None,
    org_slug: str | None,
    org_is_public: bool,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "iss": _ISSUER,
        "sub": str(user_id),
        "jti": str(uuid.uuid4()),
        "aud": _AUD_ACCESS,
        "email": email,
        "name": name,
        "wid": str(workspace_id),
        "wslug": workspace_slug,
        "wrole": workspace_role,
        "groups": [str(g) for g in groups],
        "oid": org_id,
        "oslug": org_slug,
        "opub": org_is_public,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
        "type": "access",
    }
    return _sign(payload)


def create_admin_token(user_id: uuid.UUID, email: str, name: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "iss": _ISSUER,
        "sub": str(user_id),
        "jti": str(uuid.uuid4()),
        "aud": _AUD_ADMIN,
        "email": email,
        "name": name,
        "admin": True,
        "iat": now,
        "exp": now + timedelta(minutes=settings.admin_token_expire_minutes),
        "type": "admin_access",
    }
    return _sign(payload)


def create_refresh_token(user_id: uuid.UUID, family_id: str | None = None) -> str:
    now = datetime.now(UTC)
    payload = {
        "iss": _ISSUER,
        "sub": str(user_id),
        "jti": str(uuid.uuid4()),
        "fid": family_id or str(uuid.uuid4()),
        "aud": _AUD_REFRESH,
        "iat": now,
        "exp": now + timedelta(days=settings.refresh_token_expire_days),
        "type": "refresh",
    }
    return _sign(payload)


def create_authz_token(
    user_id: uuid.UUID,
    idp_sub: str,
    workspace_id: uuid.UUID,
    workspace_slug: str,
    workspace_role: str,
    actions: list[str],
    service_name: str,
    org_id: str | None,
    org_slug: str | None,
    org_is_public: bool,
) -> str:
    """Create a short-lived authorization-only JWT.

    This token carries workspace role and RBAC actions but NOT identity.
    Identity is proven by the IdP token (validated separately).
    The idp_sub claim binds this token to a specific IdP identity.
    The svc claim binds the token to a specific service (prevents cross-service replay).
    """
    now = datetime.now(UTC)
    payload = {
        "iss": _ISSUER,
        "sub": str(user_id),
        "jti": str(uuid.uuid4()),  # Security: jti enables revocation via denylist
        "idp_sub": idp_sub,
        "svc": service_name,
        "wid": str(workspace_id),
        "wslug": workspace_slug,
        "wrole": workspace_role,
        "actions": actions,
        "oid": org_id,
        "oslug": org_slug,
        "opub": org_is_public,
        "aud": _AUD_AUTHZ,
        "iat": now,
        "exp": now + timedelta(minutes=settings.authz_token_expire_minutes),
        "type": "authz",
    }
    return _sign(payload)


def create_m2m_token(
    svc: str,
    caller: str,
    ttl_s: int,
    actions: list[str] | None = None,
    aud_target: str | None = None,
) -> str:
    """Create a short-lived no-user (machine-to-machine) realm token.

    Carries SERVICE identity only — no ``sub``/``email``/``idp_sub`` — so it can
    never be mistaken for a human. ``svc`` is the realm slug (the shared scope a
    receiving member checks against); ``caller`` is the minting member's
    ``service_name``, server-stamped for audit. ``actions=["*"]`` is full in-realm
    trust in v1; a narrowed list is enforceable later with no token-shape change.
    ``aud_target`` is reserved (off by default) for future per-call narrowing —
    when set, the receiver checks it equals its own ``service_name``.
    """
    now = datetime.now(UTC)
    payload = {
        "iss": _ISSUER,
        "aud": _AUD_M2M,
        "type": "m2m",
        "svc": svc,
        "caller": caller,
        "actions": actions if actions is not None else ["*"],
        "aud_target": aud_target,
        "jti": str(uuid.uuid4()),  # enables future denylist-based hard revoke
        "iat": now,
        "exp": now + timedelta(seconds=ttl_s),
    }
    return _sign(payload)


def decode_token(token: str, audience: str | list[str]) -> dict:
    """Decode and validate a JWT, selecting the verifying key by its kid.

    Audience is required — callers must explicitly declare expected audience.
    Algorithm is hardcoded to RS256 to prevent algorithm confusion attacks.
    A token whose kid is missing or not in the current verification set is
    rejected (strict — no legacy fallback).
    """
    kid = jwt.get_unverified_header(token).get("kid")
    keys = key_provider.verification_keys()
    if not kid or kid not in keys:
        raise jwt.InvalidTokenError("Unknown or missing key id")
    return jwt.decode(
        token,
        keys[kid],
        algorithms=["RS256"],  # Security: hardcode to prevent algorithm substitution
        audience=audience,
        issuer=_ISSUER,
    )


def _assert_algorithm() -> None:
    """Startup assertion: encoding algorithm must be RS256."""
    if settings.jwt_algorithm != "RS256":
        raise RuntimeError(
            f"jwt_algorithm must be RS256, got {settings.jwt_algorithm!r}"
        )


_assert_algorithm()
