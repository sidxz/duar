"""Short-lived authorization codes backed by Redis."""

import json
import secrets
import uuid

from src.services.token_service import get_redis

_AUTH_CODE_PREFIX = "ac:"
_AUTH_CODE_TTL = 300  # 5 minutes


async def create_auth_code(
    user_id: uuid.UUID,
    *,
    provider: str | None = None,
    client_app_id: uuid.UUID | None = None,
    code_challenge: str | None = None,
    code_challenge_method: str | None = None,
) -> str:
    """Generate a single-use authorization code and store in Redis."""
    code = secrets.token_urlsafe(32)
    r = await get_redis()
    data: dict = {"user_id": str(user_id)}
    if provider:
        data["provider"] = provider
    if client_app_id:
        data["client_app_id"] = str(client_app_id)
    if code_challenge:
        data["code_challenge"] = code_challenge
        data["code_challenge_method"] = code_challenge_method or "S256"
    payload = json.dumps(data)
    await r.set(f"{_AUTH_CODE_PREFIX}{code}", payload, ex=_AUTH_CODE_TTL)
    return code


async def peek_auth_code(code: str) -> dict | None:
    """Read auth code data without consuming it. Returns None if expired/invalid."""
    r = await get_redis()
    val = await r.get(f"{_AUTH_CODE_PREFIX}{code}")
    if not val:
        return None
    return json.loads(val)


async def consume_auth_code(code: str) -> dict | None:
    """Atomically consume an auth code (single-use). Returns None if already used/expired."""
    r = await get_redis()
    val = await r.getdel(f"{_AUTH_CODE_PREFIX}{code}")
    if not val:
        return None
    return json.loads(val)


def verify_code_challenge(code_verifier: str, code_challenge: str, method: str) -> bool:
    """Verify PKCE code_verifier against stored code_challenge."""
    import hmac

    from authlib.oauth2.rfc7636 import create_s256_code_challenge

    if method != "S256":
        return False
    return hmac.compare_digest(
        create_s256_code_challenge(code_verifier), code_challenge
    )
