"""Service layer for DB-based service app registration and key validation."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.realm import Realm
from src.models.service_app import ServiceApp
from src.services.token_service import get_redis

_CACHE_KEY = "svc:key_cache"
_CACHE_TTL = 300  # 5 minutes
_ORIGIN_CACHE_KEY = "svc:origin_cache"


def _encode_cache(service_name: str, app_id: uuid.UUID, realm_slug: str | None) -> str:
    return f"{service_name}:{app_id}:{realm_slug or ''}"


def _decode_cache(value: str) -> tuple[str, uuid.UUID, str | None]:
    """Parse a cache value. Tolerates legacy 2-part ('svc:app_id') entries."""
    parts = value.split(":", 2)
    if len(parts) == 2:
        service_name, app_id_str = parts
        return service_name, uuid.UUID(app_id_str), None
    service_name, app_id_str, realm_slug = parts
    return service_name, uuid.UUID(app_id_str), (realm_slug or None)


def _generate_key() -> tuple[str, str, str]:
    """Generate a service API key.

    Returns (plaintext_key, sha256_hex, display_prefix).
    """
    raw = secrets.token_urlsafe(32)
    plaintext = f"sk_{raw}"
    sha = hashlib.sha256(plaintext.encode()).hexdigest()
    prefix = f"sk_{raw[:4]}****"
    return plaintext, sha, prefix


async def create_service_app(
    db: AsyncSession,
    name: str,
    service_name: str,
    created_by: uuid.UUID | None = None,
    allowed_origins: list[str] | None = None,
    allowed_idp_audiences: list[str] | None = None,
) -> tuple[ServiceApp, str]:
    """Create a new service app. Returns (app, plaintext_key)."""
    # Symmetric with realm_service.create_realm — service_names and realm
    # slugs share the authz `svc` claim namespace (effective_scope). Same
    # advisory lock keyed on the name, so a concurrent create_realm with the
    # same name serializes and the collision check can't be raced.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
        {"k": f"duar:scope:{service_name}"},
    )
    collision = await db.execute(
        select(Realm.id).where(Realm.slug == service_name).limit(1)
    )
    if collision.scalar_one_or_none():
        raise ValueError(
            f"Service name '{service_name}' is already in use as a realm slug"
        )
    plaintext, sha, prefix = _generate_key()
    app = ServiceApp(
        id=uuid.uuid4(),
        name=name,
        service_name=service_name,
        key_hash=sha,
        key_prefix=prefix,
        created_by=created_by,
        allowed_origins=allowed_origins or [],
        allowed_idp_audiences=allowed_idp_audiences or [],
    )
    db.add(app)
    await db.flush()
    return app, plaintext


async def validate_key(
    raw_key: str, db: AsyncSession
) -> tuple[str, uuid.UUID, str | None] | None:
    """Validate a raw API key. Returns (service_name, app_id, realm_slug) or None."""
    sha = hashlib.sha256(raw_key.encode()).hexdigest()

    r = await get_redis()
    cached = await r.hget(_CACHE_KEY, sha)
    if cached:
        svc, app_id, realm_slug = _decode_cache(cached)
        if not await _touch_last_used(db, app_id):
            return None
        return svc, app_id, realm_slug

    await _rebuild_cache(db)

    cached = await r.hget(_CACHE_KEY, sha)
    if cached:
        svc, app_id, realm_slug = _decode_cache(cached)
        if not await _touch_last_used(db, app_id):
            return None
        return svc, app_id, realm_slug

    return None


async def rotate_key(db: AsyncSession, app_id: uuid.UUID) -> tuple[ServiceApp, str]:
    """Rotate the API key for a service app. Returns (app, new_plaintext_key)."""
    app = await db.get(ServiceApp, app_id)
    if not app:
        raise ValueError("Service app not found")
    plaintext, sha, prefix = _generate_key()
    app.key_hash = sha
    app.key_prefix = prefix
    await db.flush()
    return app, plaintext


async def list_service_apps(db: AsyncSession) -> list[ServiceApp]:
    result = await db.execute(select(ServiceApp).order_by(ServiceApp.created_at.desc()))
    return list(result.scalars().all())


async def get_service_app(db: AsyncSession, app_id: uuid.UUID) -> ServiceApp | None:
    return await db.get(ServiceApp, app_id)


async def get_idp_audiences(db: AsyncSession, app_id: uuid.UUID) -> tuple[str, ...]:
    """The app's registered IdP audience(s) (OIDC client_id(s)) for per-app token binding
    at /authz/resolve. Empty tuple when unset (=> fall back to deployment-wide audience)."""
    app = await db.get(ServiceApp, app_id)
    return tuple(app.allowed_idp_audiences) if app and app.allowed_idp_audiences else ()


async def update_service_app(
    db: AsyncSession,
    app_id: uuid.UUID,
    name: str | None = None,
    is_active: bool | None = None,
    allowed_origins: list[str] | None = None,
    allowed_idp_audiences: list[str] | None = None,
) -> ServiceApp:
    app = await db.get(ServiceApp, app_id)
    if not app:
        raise ValueError("Service app not found")
    if name is not None:
        app.name = name
    if is_active is not None:
        app.is_active = is_active
    if allowed_origins is not None:
        app.allowed_origins = allowed_origins
    if allowed_idp_audiences is not None:
        app.allowed_idp_audiences = allowed_idp_audiences
    await db.flush()
    return app


async def delete_service_app(db: AsyncSession, app_id: uuid.UUID) -> None:
    app = await db.get(ServiceApp, app_id)
    if not app:
        raise ValueError("Service app not found")
    await db.delete(app)
    await db.flush()


async def has_active_apps(db: AsyncSession) -> bool:
    result = await db.execute(
        select(ServiceApp.id).where(ServiceApp.is_active == True).limit(1)  # noqa: E712
    )
    return result.scalar_one_or_none() is not None


async def validate_origin(
    origin: str, db: AsyncSession
) -> tuple[str, uuid.UUID] | None:
    """Validate a request origin against service app allowed_origins.
    Returns (service_name, app_id) or None.
    """
    r = await get_redis()
    cached = await r.hget(_ORIGIN_CACHE_KEY, origin)
    if cached:
        svc, app_id_str = cached.split(":", 1)
        return svc, uuid.UUID(app_id_str)

    await _rebuild_origin_cache(db)

    cached = await r.hget(_ORIGIN_CACHE_KEY, origin)
    if cached:
        svc, app_id_str = cached.split(":", 1)
        return svc, uuid.UUID(app_id_str)

    return None


# ── Internal helpers ─────────────────────────────────────────────────


async def _rebuild_cache(db: AsyncSession) -> None:
    """Load all active apps (+ their realm slug) into the Redis hash cache."""
    r = await get_redis()
    result = await db.execute(
        select(ServiceApp, Realm.slug)
        .outerjoin(Realm, ServiceApp.realm_id == Realm.id)
        .where(ServiceApp.is_active == True)  # noqa: E712
    )
    rows = result.all()
    pipe = r.pipeline()
    pipe.delete(_CACHE_KEY)
    for app, realm_slug in rows:
        pipe.hset(
            _CACHE_KEY,
            app.key_hash,
            _encode_cache(app.service_name, app.id, realm_slug),
        )
    pipe.expire(_CACHE_KEY, _CACHE_TTL)
    await pipe.execute()


async def _rebuild_origin_cache(db: AsyncSession) -> None:
    """Load all active apps' allowed_origins into a Redis hash."""
    r = await get_redis()
    result = await db.execute(
        select(ServiceApp).where(ServiceApp.is_active == True)  # noqa: E712
    )
    apps = result.scalars().all()
    pipe = r.pipeline()
    pipe.delete(_ORIGIN_CACHE_KEY)
    for app in apps:
        for origin in app.allowed_origins or []:
            pipe.hset(_ORIGIN_CACHE_KEY, origin, f"{app.service_name}:{app.id}")
    pipe.expire(_ORIGIN_CACHE_KEY, _CACHE_TTL)
    await pipe.execute()


async def invalidate_cache() -> None:
    """Clear the key/origin caches. Routes must call this AFTER db.commit():
    invalidating pre-commit lets a concurrent cache-miss rebuild repopulate from
    the not-yet-committed DB state (READ COMMITTED), resurrecting e.g. a
    rotated-out key for a full _CACHE_TTL. Mutating service functions therefore
    do NOT invalidate themselves — same contract as cors.refresh_origins()."""
    r = await get_redis()
    await r.delete(_CACHE_KEY, _ORIGIN_CACHE_KEY)


async def _touch_last_used(db: AsyncSession, app_id: uuid.UUID) -> bool:
    """Update last_used_at timestamp (best-effort). Returns False if app is inactive."""
    app = await db.get(ServiceApp, app_id)
    if not app or not app.is_active:
        await invalidate_cache()
        return False
    app.last_used_at = datetime.now(UTC)
    await db.commit()
    return True
