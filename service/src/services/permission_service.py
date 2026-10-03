import uuid

from sqlalchemy import func, select, union
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.group import Group
from src.models.permission import ResourcePermission, ResourceShare
from src.models.user import User


async def register_resource(
    db: AsyncSession,
    service_name: str,
    resource_type: str,
    resource_id: uuid.UUID,
    workspace_id: uuid.UUID,
    owner_id: uuid.UUID,
    visibility: str = "workspace",
) -> ResourcePermission:
    # Validate the owner belongs to the workspace, symmetric with
    # share_resource's grantee check — prevents registering a resource owned by
    # a user outside the workspace (data-integrity / cross-workspace hardening).
    from src.models.workspace import WorkspaceMembership

    member_stmt = select(WorkspaceMembership).where(
        WorkspaceMembership.workspace_id == workspace_id,
        WorkspaceMembership.user_id == owner_id,
    )
    if not (await db.execute(member_stmt)).scalar_one_or_none():
        raise ValueError("Owner is not a member of this workspace")

    # Atomic upsert: ON CONFLICT DO NOTHING avoids race conditions
    stmt = (
        pg_insert(ResourcePermission)
        .values(
            service_name=service_name,
            resource_type=resource_type,
            resource_id=resource_id,
            workspace_id=workspace_id,
            owner_id=owner_id,
            visibility=visibility,
        )
        .on_conflict_do_nothing(
            index_elements=["service_name", "resource_type", "resource_id"],
        )
    )
    await db.execute(stmt)
    await db.commit()
    # Re-fetch to return the existing or newly inserted record
    return await get_resource_permission(db, service_name, resource_type, resource_id)


async def get_permission_by_id(
    db: AsyncSession,
    permission_id: uuid.UUID,
) -> ResourcePermission | None:
    stmt = (
        select(ResourcePermission)
        .options(selectinload(ResourcePermission.shares))
        .where(ResourcePermission.id == permission_id)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_resource_permission(
    db: AsyncSession,
    service_name: str,
    resource_type: str,
    resource_id: uuid.UUID,
) -> ResourcePermission | None:
    stmt = (
        select(ResourcePermission)
        .options(selectinload(ResourcePermission.shares))
        .where(
            ResourcePermission.service_name == service_name,
            ResourcePermission.resource_type == resource_type,
            ResourcePermission.resource_id == resource_id,
        )
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def update_visibility(
    db: AsyncSession,
    permission_id: uuid.UUID,
    visibility: str,
) -> ResourcePermission:
    stmt = (
        select(ResourcePermission)
        .options(selectinload(ResourcePermission.shares))
        .where(ResourcePermission.id == permission_id)
    )
    result = await db.execute(stmt)
    perm = result.scalar_one_or_none()
    if not perm:
        raise ValueError("Resource permission not found")
    perm.visibility = visibility
    await db.commit()
    return perm


async def deregister_resource(
    db: AsyncSession,
    service_name: str,
    resource_type: str,
    resource_id: uuid.UUID,
) -> None:
    """Delete a resource permission and all its shares (FK cascade)."""
    perm = await get_resource_permission(db, service_name, resource_type, resource_id)
    if not perm:
        raise ValueError("Resource permission not found")
    await db.delete(perm)
    await db.commit()


async def purge_service_permissions(
    db: AsyncSession,
    service_name: str,
) -> int:
    """Delete all resource permissions for a service. Returns count deleted.

    Does NOT commit — caller is responsible for committing the transaction.
    This allows atomic composition with other operations (e.g. deleting the service app).
    """
    from sqlalchemy import delete

    result = await db.execute(
        delete(ResourcePermission).where(
            ResourcePermission.service_name == service_name,
        )
    )
    return result.rowcount


async def share_resource(
    db: AsyncSession,
    permission_id: uuid.UUID,
    grantee_type: str,
    grantee_id: uuid.UUID,
    permission: str,
    granted_by: uuid.UUID,
) -> ResourceShare:
    # Validate grantee belongs to the resource's workspace
    perm = await get_permission_by_id(db, permission_id)
    if not perm:
        raise ValueError("Resource permission not found")

    if grantee_type == "user":
        from src.models.workspace import WorkspaceMembership

        stmt = select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == perm.workspace_id,
            WorkspaceMembership.user_id == grantee_id,
        )
        if not (await db.execute(stmt)).scalar_one_or_none():
            raise ValueError("Grantee is not a member of this workspace")
    elif grantee_type == "group":
        from src.models.group import Group

        group = await db.get(Group, grantee_id)
        if not group or group.workspace_id != perm.workspace_id:
            raise ValueError("Group does not belong to this workspace")

    # Atomic upsert on uq_resource_share: re-sharing the same grantee is a
    # permission upgrade/downgrade, not an IntegrityError → 500.
    stmt = (
        pg_insert(ResourceShare)
        .values(
            resource_permission_id=permission_id,
            grantee_type=grantee_type,
            grantee_id=grantee_id,
            permission=permission,
            granted_by=granted_by,
        )
        .on_conflict_do_update(
            constraint="uq_resource_share",
            set_={
                "permission": permission,
                "granted_by": granted_by,
                "granted_at": func.now(),
            },
        )
        .returning(ResourceShare.id)
    )
    share_id = (await db.execute(stmt)).scalar_one()
    await db.commit()
    return await db.get(ResourceShare, share_id)


async def revoke_share(
    db: AsyncSession,
    permission_id: uuid.UUID,
    grantee_type: str,
    grantee_id: uuid.UUID,
) -> None:
    stmt = select(ResourceShare).where(
        ResourceShare.resource_permission_id == permission_id,
        ResourceShare.grantee_type == grantee_type,
        ResourceShare.grantee_id == grantee_id,
    )
    result = await db.execute(stmt)
    share = result.scalar_one_or_none()
    if not share:
        raise ValueError("Share not found")
    await db.delete(share)
    await db.commit()


async def get_enriched_resource_permission(
    db: AsyncSession,
    service_name: str,
    resource_type: str,
    resource_id: uuid.UUID,
) -> dict | None:
    """Get resource permission with user profiles resolved inline."""
    perm = await get_resource_permission(db, service_name, resource_type, resource_id)
    if not perm:
        return None

    # Collect IDs to resolve in batch queries
    user_ids: set[uuid.UUID] = set()
    group_ids: set[uuid.UUID] = set()
    if perm.owner_id:
        user_ids.add(perm.owner_id)
    for share in perm.shares:
        if share.grantee_type == "user":
            user_ids.add(share.grantee_id)
        elif share.grantee_type == "group":
            group_ids.add(share.grantee_id)
        if share.granted_by:
            user_ids.add(share.granted_by)

    # Batch resolve user profiles
    profiles: dict[uuid.UUID, User] = {}
    if user_ids:
        stmt = select(User).where(User.id.in_(user_ids))
        result = await db.execute(stmt)
        for u in result.scalars().all():
            profiles[u.id] = u

    # Batch resolve group names
    group_names: dict[uuid.UUID, str] = {}
    if group_ids:
        stmt = select(Group).where(Group.id.in_(group_ids))
        result = await db.execute(stmt)
        for g in result.scalars().all():
            group_names[g.id] = g.name

    owner = profiles.get(perm.owner_id) if perm.owner_id else None
    enriched_shares = []
    for share in perm.shares:
        if share.grantee_type == "user":
            grantee_user = profiles.get(share.grantee_id)
            grantee_name = grantee_user.name if grantee_user else None
            grantee_email = grantee_user.email if grantee_user else None
        elif share.grantee_type == "group":
            grantee_name = group_names.get(share.grantee_id)
            grantee_email = None  # groups don't have emails
        else:
            grantee_name = None
            grantee_email = None

        granter = profiles.get(share.granted_by) if share.granted_by else None
        enriched_shares.append(
            {
                "id": share.id,
                "grantee_type": share.grantee_type,
                "grantee_id": share.grantee_id,
                "grantee_name": grantee_name,
                "grantee_email": grantee_email,
                "permission": share.permission,
                "granted_by": share.granted_by,
                "granted_by_name": granter.name if granter else None,
                "granted_at": share.granted_at,
            }
        )

    return {
        "id": perm.id,
        "service_name": perm.service_name,
        "resource_type": perm.resource_type,
        "resource_id": perm.resource_id,
        "workspace_id": perm.workspace_id,
        "owner_id": perm.owner_id,
        "owner_name": owner.name if owner else None,
        "owner_email": owner.email if owner else None,
        "visibility": perm.visibility,
        "created_at": perm.created_at,
        "shares": enriched_shares,
    }


async def check_permission(
    db: AsyncSession,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    workspace_role: str,
    group_ids: list[uuid.UUID],
    service_name: str,
    resource_type: str,
    resource_id: uuid.UUID,
    action: str,
) -> bool:
    """
    Permission resolution:
    1. Must be workspace member (caller ensures this via JWT)
    2. Is entity owner? -> full access
    3. Is workspace admin/owner? -> full access
    4. Is entity workspace-visible? -> apply workspace role
    5. Check direct user shares
    6. Check group shares
    7. Default: deny
    """
    perm = await get_resource_permission(db, service_name, resource_type, resource_id)
    if not perm:
        return False

    # Must be in the same workspace
    if perm.workspace_id != workspace_id:
        return False

    # Owner has full access
    if perm.owner_id == user_id:
        return True

    # Workspace admin/owner has full access
    if workspace_role in ("admin", "owner"):
        return True

    # Workspace-visible: apply role-based access
    if perm.visibility == "workspace":
        if action == "view":
            return True  # All workspace members can view
        if action == "edit" and workspace_role == "editor":
            return True

    # Check direct user shares
    stmt = select(ResourceShare).where(
        ResourceShare.resource_permission_id == perm.id,
        ResourceShare.grantee_type == "user",
        ResourceShare.grantee_id == user_id,
    )
    result = await db.execute(stmt)
    user_share = result.scalar_one_or_none()
    if user_share:
        if action == "view":
            return True
        if action == "edit" and user_share.permission == "edit":
            return True

    # Check group shares
    if group_ids:
        stmt = select(ResourceShare).where(
            ResourceShare.resource_permission_id == perm.id,
            ResourceShare.grantee_type == "group",
            ResourceShare.grantee_id.in_(group_ids),
        )
        result = await db.execute(stmt)
        group_shares = result.scalars().all()
        for gs in group_shares:
            if action == "view":
                return True
            if action == "edit" and gs.permission == "edit":
                return True

    return False


async def lookup_accessible_resources(
    db: AsyncSession,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    workspace_role: str,
    group_ids: list[uuid.UUID],
    service_name: str,
    resource_type: str,
    action: str,
    limit: int | None = None,
) -> tuple[list[uuid.UUID], bool]:
    """
    Return (resource_ids, has_full_access) for the given user context.

    Admin/owner roles get has_full_access=True. If no limit is set, resource_ids
    is empty (caller should skip filtering). With a limit, returns up to that
    many IDs even for full-access users.
    """
    is_privileged = workspace_role in ("admin", "owner")

    if is_privileged and limit is None:
        return [], True

    # Base filter: same workspace, service, type
    base_filter = [
        ResourcePermission.workspace_id == workspace_id,
        ResourcePermission.service_name == service_name,
        ResourcePermission.resource_type == resource_type,
    ]

    if is_privileged:
        # Admin/owner: all resources in workspace for this service/type
        stmt = select(ResourcePermission.resource_id).where(*base_filter).limit(limit)
        result = await db.execute(stmt)
        return list(result.scalars().all()), True

    # Non-privileged: UNION of all access paths
    # 1. Resources owned by user
    owned = select(ResourcePermission.resource_id).where(
        *base_filter,
        ResourcePermission.owner_id == user_id,
    )

    queries = [owned]

    # 2. Workspace-visible resources
    if action == "view":
        ws_visible = select(ResourcePermission.resource_id).where(
            *base_filter,
            ResourcePermission.visibility == "workspace",
        )
        queries.append(ws_visible)
    elif action == "edit" and workspace_role == "editor":
        ws_editable = select(ResourcePermission.resource_id).where(
            *base_filter,
            ResourcePermission.visibility == "workspace",
        )
        queries.append(ws_editable)

    # 3. Direct user shares
    if action == "view":
        user_shared = (
            select(ResourcePermission.resource_id)
            .join(
                ResourceShare,
                ResourceShare.resource_permission_id == ResourcePermission.id,
            )
            .where(
                *base_filter,
                ResourceShare.grantee_type == "user",
                ResourceShare.grantee_id == user_id,
            )
        )
    else:
        user_shared = (
            select(ResourcePermission.resource_id)
            .join(
                ResourceShare,
                ResourceShare.resource_permission_id == ResourcePermission.id,
            )
            .where(
                *base_filter,
                ResourceShare.grantee_type == "user",
                ResourceShare.grantee_id == user_id,
                ResourceShare.permission == "edit",
            )
        )
    queries.append(user_shared)

    # 4. Group shares
    if group_ids:
        if action == "view":
            group_shared = (
                select(ResourcePermission.resource_id)
                .join(
                    ResourceShare,
                    ResourceShare.resource_permission_id == ResourcePermission.id,
                )
                .where(
                    *base_filter,
                    ResourceShare.grantee_type == "group",
                    ResourceShare.grantee_id.in_(group_ids),
                )
            )
        else:
            group_shared = (
                select(ResourcePermission.resource_id)
                .join(
                    ResourceShare,
                    ResourceShare.resource_permission_id == ResourcePermission.id,
                )
                .where(
                    *base_filter,
                    ResourceShare.grantee_type == "group",
                    ResourceShare.grantee_id.in_(group_ids),
                    ResourceShare.permission == "edit",
                )
            )
        queries.append(group_shared)

    combined = union(*queries)
    stmt = select(combined.c.resource_id)
    if limit is not None:
        stmt = stmt.limit(limit)

    result = await db.execute(stmt)
    return list(result.scalars().all()), False
