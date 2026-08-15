# service/src/services/realm_service.py
"""Service layer for realms (trusted app groups) + membership."""

import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.realm import Realm
from src.models.service_app import ServiceApp


async def create_realm(
    db: AsyncSession,
    *,
    name: str,
    slug: str,
    m2m_ttl_s: int = 300,
    created_by: uuid.UUID | None = None,
) -> Realm:
    # Realm slugs and standalone service_names share the authz `svc` claim
    # namespace (ServiceKeyContext.effective_scope) — a collision would let
    # tokens minted for one trust domain verify at the other. Take a
    # transaction advisory lock keyed on the name first (create_service_app
    # takes the same lock for the same name), so two concurrent creates of the
    # same name serialize and the check below can't be raced.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
        {"k": f"duar:scope:{slug}"},
    )
    collision = await db.execute(
        select(ServiceApp.id).where(ServiceApp.service_name == slug).limit(1)
    )
    if collision.scalar_one_or_none():
        raise ValueError(f"Slug '{slug}' is already in use as a service name")
    realm = Realm(
        id=uuid.uuid4(),
        name=name,
        slug=slug,
        m2m_ttl_s=m2m_ttl_s,
        created_by=created_by,
    )
    db.add(realm)
    await db.flush()
    return realm


async def get_realm(db: AsyncSession, realm_id: uuid.UUID) -> Realm | None:
    return await db.get(Realm, realm_id)


async def get_realm_by_slug(db: AsyncSession, slug: str) -> Realm | None:
    """Resolve a realm by its shared-scope slug. Used by /realm/whoami (for the
    display name) and /realm/m2m-token (for is_active + m2m_ttl_s)."""
    result = await db.execute(select(Realm).where(Realm.slug == slug))
    return result.scalar_one_or_none()


async def list_realms(db: AsyncSession) -> list[Realm]:
    result = await db.execute(select(Realm).order_by(Realm.created_at.desc()))
    return list(result.scalars().all())


async def add_member(
    db: AsyncSession, realm_id: uuid.UUID, service_app_id: uuid.UUID
) -> ServiceApp:
    """Assign a service app to a realm. The single FK enforces one-realm-max:
    re-assigning simply overwrites the prior realm. The service-key cache stores
    the member's realm slug — the route invalidates it after commit
    (service_app_service.invalidate_cache)."""
    app = await db.get(ServiceApp, service_app_id)
    if not app:
        raise ValueError("Service app not found")
    app.realm_id = realm_id
    await db.flush()
    return app


async def remove_member(db: AsyncSession, service_app_id: uuid.UUID) -> ServiceApp:
    app = await db.get(ServiceApp, service_app_id)
    if not app:
        raise ValueError("Service app not found")
    app.realm_id = None
    await db.flush()
    return app


async def update_realm(
    db: AsyncSession,
    realm_id: uuid.UUID,
    *,
    name: str | None = None,
    m2m_ttl_s: int | None = None,
    is_active: bool | None = None,
) -> Realm | None:
    """Patch a realm's mutable fields. Slug is intentionally NOT updatable (it keys
    effective_scope). Returns None if the realm doesn't exist."""
    realm = await db.get(Realm, realm_id)
    if realm is None:
        return None
    if name is not None:
        realm.name = name
    if m2m_ttl_s is not None:
        realm.m2m_ttl_s = m2m_ttl_s
    if is_active is not None:
        realm.is_active = is_active
    await db.flush()
    return realm


async def delete_realm(db: AsyncSession, realm_id: uuid.UUID) -> bool:
    """Delete a realm. The service_apps.realm_id FK is ON DELETE SET NULL, so members
    revert to standalone — the route invalidates the service-key cache after commit
    (it stores realm slugs)."""
    realm = await db.get(Realm, realm_id)
    if realm is None:
        return False
    await db.delete(realm)
    await db.flush()
    return True


async def list_members(db: AsyncSession, realm_id: uuid.UUID) -> list[ServiceApp]:
    result = await db.execute(
        select(ServiceApp)
        .where(ServiceApp.realm_id == realm_id)
        .order_by(ServiceApp.name)
    )
    return list(result.scalars().all())


async def service_app_has_grants(db: AsyncSession, service_name: str) -> bool:
    """True if the service already has RBAC actions or resource permissions under its
    own ``service_name``. Joining a realm won't surface those under the new shared
    scope (v1 has no auto-migrate), so the admin UI warns before adding."""
    from src.models.permission import ResourcePermission
    from src.models.role import ServiceAction

    actions = await db.execute(
        select(ServiceAction.id)
        .where(ServiceAction.service_name == service_name)
        .limit(1)
    )
    if actions.first() is not None:
        return True
    perms = await db.execute(
        select(ResourcePermission.id)
        .where(ResourcePermission.service_name == service_name)
        .limit(1)
    )
    return perms.first() is not None
