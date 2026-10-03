"""Refresh token rotation and access token revocation via Redis."""

import json
import uuid
from datetime import UTC, datetime

import redis.asyncio as redis

from src.config import settings

_redis: redis.Redis | None = None

# Key prefixes
_REFRESH_PREFIX = "rt:"  # rt:{jti} → JSON {user_id, family_id}
_FAMILY_PREFIX = "rtf:"  # rtf:{family_id} → set of refresh jtis
_FAMILY_AJTIS_PREFIX = "rta:"  # rta:{family_id} → set of access jtis (all rotations)
_BLACKLIST_PREFIX = "bl:"  # bl:{jti} → "1"
_CLIENT_APP_PREFIX = "cap:"  # cap:{client_app_id} → set of family_ids
_USER_FAMILIES_PREFIX = "uf:"  # uf:{user_id} → set of family_ids
_REFRESH_CTX_PREFIX = "rctx:"  # rctx:{family_id} → JSON {ip, ua} last seen


async def swap_refresh_context(family_id: str, ip: str, user_agent: str) -> dict | None:
    """Record the client context (ip + user-agent) last seen for a token family.

    Keyed per family (i.e. per device/session), so multiple devices refreshing
    for the same user never cross-trigger. Returns the previous context iff it
    differs from the new one — a changed context on the SAME family is the
    anomaly signal (possible token theft) — else None.
    """
    r = await get_redis()
    key = f"{_REFRESH_CTX_PREFIX}{family_id}"
    new = json.dumps({"ip": ip, "ua": user_agent}, sort_keys=True)
    old = await r.get(key)
    await r.set(key, new, ex=settings.refresh_token_expire_days * 86400)
    if old is None or old == new:
        return None
    try:
        return json.loads(old)
    except ValueError:
        return None


async def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.from_url(
            settings.redis_url, decode_responses=True, **settings.redis_ssl_kwargs
        )
    return _redis


async def store_refresh_token(
    jti: str,
    user_id: uuid.UUID,
    family_id: str,
    workspace_id: uuid.UUID,
    client_app_id: uuid.UUID | None = None,
    access_jti: str | None = None,
) -> None:
    """Store a refresh token with its family for rotation tracking."""
    r = await get_redis()
    ttl = settings.refresh_token_expire_days * 86400
    pipe = r.pipeline()
    # Format: user_id:family_id:workspace_id:client_app_id:access_jti
    # Empty segments use empty string as placeholder
    cap = str(client_app_id) if client_app_id else ""
    ajti = access_jti or ""
    val = f"{user_id}:{family_id}:{workspace_id}:{cap}:{ajti}"
    pipe.set(f"{_REFRESH_PREFIX}{jti}", val, ex=ttl)
    pipe.sadd(f"{_FAMILY_PREFIX}{family_id}", jti)
    pipe.expire(f"{_FAMILY_PREFIX}{family_id}", ttl)
    # Track every access_jti ever minted in this family so revoke_token_family
    # can blacklist pre-rotation tokens. consume_refresh_token uses getdel on
    # rt:{jti} which erases the paired access_jti from the refresh record —
    # without this parallel set, revocation only catches the newest token.
    if access_jti:
        pipe.sadd(f"{_FAMILY_AJTIS_PREFIX}{family_id}", access_jti)
        pipe.expire(f"{_FAMILY_AJTIS_PREFIX}{family_id}", ttl)
    pipe.sadd(f"{_USER_FAMILIES_PREFIX}{user_id}", family_id)
    pipe.expire(f"{_USER_FAMILIES_PREFIX}{user_id}", ttl)
    if client_app_id:
        pipe.sadd(f"{_CLIENT_APP_PREFIX}{client_app_id}", family_id)
        pipe.expire(f"{_CLIENT_APP_PREFIX}{client_app_id}", ttl)
    await pipe.execute()


async def consume_refresh_token(
    jti: str,
) -> tuple[uuid.UUID, str, uuid.UUID, uuid.UUID | None] | None:
    """Consume a refresh token (one-time use).

    Returns (user_id, family_id, workspace_id, client_app_id) if valid,
    None if already consumed/expired.
    """
    r = await get_redis()
    val = await r.getdel(f"{_REFRESH_PREFIX}{jti}")
    if not val:
        return None
    # Format: user_id:family_id:workspace_id:client_app_id:access_jti
    # (legacy format without access_jti is also supported)
    parts = val.split(":")
    user_id = uuid.UUID(parts[0])
    family_id = parts[1]
    workspace_id = uuid.UUID(parts[2])
    client_app_id = uuid.UUID(parts[3]) if len(parts) > 3 and parts[3] else None
    return user_id, family_id, workspace_id, client_app_id


async def revoke_token_family(family_id: str) -> int:
    """Revoke all refresh tokens in a family (theft detection).

    Also blacklists every access token ever minted in the family so that
    pre-rotation access tokens captured by an attacker (via XSS, device theft,
    etc.) cannot be used for the remainder of their ~15-minute TTL after the
    family has been killed.

    Returns the number of refresh tokens revoked.
    """
    r = await get_redis()
    jtis = await r.smembers(f"{_FAMILY_PREFIX}{family_id}")
    ajtis = await r.smembers(f"{_FAMILY_AJTIS_PREFIX}{family_id}")
    if not jtis and not ajtis:
        return 0

    pipe = r.pipeline()
    for jti in jtis:
        pipe.delete(f"{_REFRESH_PREFIX}{jti}")
    pipe.delete(f"{_FAMILY_PREFIX}{family_id}")
    pipe.delete(f"{_FAMILY_AJTIS_PREFIX}{family_id}")
    results = await pipe.execute()

    # Blacklist every access_jti ever issued in this family (default 900s /
    # 15 min TTL). Reading the parallel set catches access tokens whose
    # refresh companions were already consumed (and thus getdel'd) before
    # revocation fires.
    access_ttl = settings.access_token_expire_minutes * 60
    for access_jti in ajtis:
        await r.set(f"{_BLACKLIST_PREFIX}{access_jti}", "1", ex=access_ttl)

    return sum(1 for x in results[: len(jtis)] if x)


async def revoke_all_user_tokens(user_id: str) -> int:
    """Revoke all token families for a user. Returns total tokens revoked."""
    r = await get_redis()
    family_ids = await r.smembers(f"{_USER_FAMILIES_PREFIX}{user_id}")
    if not family_ids:
        return 0
    total = 0
    for fid in family_ids:
        total += await revoke_token_family(fid)
    await r.delete(f"{_USER_FAMILIES_PREFIX}{user_id}")
    return total


async def revoke_app_tokens(client_app_id: str) -> int:
    """Revoke all token families associated with a client app. Returns total tokens revoked."""
    r = await get_redis()
    family_ids = await r.smembers(f"{_CLIENT_APP_PREFIX}{client_app_id}")
    if not family_ids:
        return 0
    total = 0
    for fid in family_ids:
        total += await revoke_token_family(fid)
    await r.delete(f"{_CLIENT_APP_PREFIX}{client_app_id}")
    return total


async def blacklist_access_token(jti: str, exp: int) -> None:
    """Add an access token's jti to the denylist until it expires."""
    r = await get_redis()
    remaining = exp - int(datetime.now(UTC).timestamp())
    if remaining > 0:
        await r.set(f"{_BLACKLIST_PREFIX}{jti}", "1", ex=remaining)


async def is_access_token_blacklisted(jti: str) -> bool:
    """Check if an access token has been revoked."""
    r = await get_redis()
    return await r.exists(f"{_BLACKLIST_PREFIX}{jti}") > 0


# ── User deactivation tracking ──────────────────────────────────────

_DEACTIVATED_PREFIX = "deactivated:"


async def mark_user_deactivated(user_id: str) -> None:
    """Record that a user has been deactivated for fast Redis-based checks."""
    r = await get_redis()
    await r.set(f"{_DEACTIVATED_PREFIX}{user_id}", "1")


async def mark_user_activated(user_id: str) -> None:
    """Remove the deactivation flag when a user is re-activated."""
    r = await get_redis()
    await r.delete(f"{_DEACTIVATED_PREFIX}{user_id}")


async def is_user_deactivated(user_id: str) -> bool:
    """Check if a user is flagged as deactivated in Redis."""
    r = await get_redis()
    return bool(await r.exists(f"{_DEACTIVATED_PREFIX}{user_id}"))
