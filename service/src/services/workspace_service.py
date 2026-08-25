import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models.group import Group, GroupMembership
from src.models.permission import ResourcePermission, ResourceShare
from src.models.role import Role, UserRole
from src.models.user import User
from src.models.workspace import Workspace, WorkspaceMembership
from src.services import organization_service, token_service


async def create_workspace(
    db: AsyncSession,
    name: str,
    slug: str,
    created_by: uuid.UUID,
    description: str | None = None,
) -> Workspace:
    workspace = Workspace(
        name=name, slug=slug, description=description, created_by=created_by
    )
    db.add(workspace)
    try:
        await db.flush()
    except IntegrityError:
        raise ValueError("A workspace with this slug already exists") from None

    # Creator becomes owner
    membership = WorkspaceMembership(
        workspace_id=workspace.id, user_id=created_by, role="owner"
    )
    db.add(membership)
    await db.commit()
    return workspace


class SelfServeDisabled(Exception):
    """SELF_SERVE_ENABLED is off."""


class SelfServeCapReached(Exception):
    """The user already created SELF_SERVE_MAX_WORKSPACES_PER_USER workspaces."""


class SelfServeThrottled(Exception):
    """Instance-wide SELF_SERVE_MAX_CREATES_PER_HOUR reached."""


_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """lowercase; runs of non-[a-z0-9] -> '-'; trimmed; <= 40 chars; 'ws' if empty."""
    base = _NON_SLUG.sub("-", name.lower()).strip("-")[:40].strip("-")
    return base or "ws"


async def count_created_by(db: AsyncSession, user_id: uuid.UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(Workspace)
        .where(Workspace.created_by == user_id)
    )
    return int(await db.scalar(stmt) or 0)


async def get_member_role(
    db: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID
) -> str | None:
    stmt = select(WorkspaceMembership.role).where(
        WorkspaceMembership.workspace_id == workspace_id,
        WorkspaceMembership.user_id == user_id,
    )
    return await db.scalar(stmt)


async def list_admin_workspaces(
    db: AsyncSession, user_id: uuid.UUID
) -> list[tuple[Workspace, str]]:
    """Workspaces where the user is owner or admin, with the role."""
    stmt = (
        select(Workspace, WorkspaceMembership.role)
        .join(WorkspaceMembership)
        .where(
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.role.in_(("owner", "admin")),
        )
        .order_by(Workspace.created_at)
    )
    return [(ws, role) for ws, role in (await db.execute(stmt)).all()]


async def create_self_serve(
    db: AsyncSession,
    user: User,
    name: str,
    *,
    slug: str | None = None,
    now: datetime | None = None,
) -> Workspace:
    """Self-serve workspace creation: flag -> per-user cap -> hourly breaker -> create.

    The user row is locked FOR UPDATE for the duration so concurrent requests
    from one user cannot both pass the cap (no-op on SQLite). The breaker counts
    *successful* creations instance-wide, so unauthenticated traffic cannot
    exhaust it. ``slug=None`` generates ``slugify(name)-<4 hex>``; a caller-supplied
    slug (proxy-mode API) is used verbatim and a collision is a ValueError.
    """
    if not settings.self_serve_enabled:
        raise SelfServeDisabled()
    now = now or datetime.now(UTC)
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())
    if (
        await count_created_by(db, user.id)
        >= settings.self_serve_max_workspaces_per_user
    ):
        raise SelfServeCapReached()
    recent = await db.scalar(
        select(func.count())
        .select_from(Workspace)
        .where(Workspace.created_at > now - timedelta(hours=1))
    )
    if int(recent or 0) >= settings.self_serve_max_creates_per_hour:
        raise SelfServeThrottled()
    if slug is not None:
        return await create_workspace(db, name=name, slug=slug, created_by=user.id)
    for _ in range(3):
        try:
            return await create_workspace(
                db,
                name=name,
                slug=f"{slugify(name)}-{secrets.token_hex(2)}",
                created_by=user.id,
            )
        except ValueError:
            # 1-in-65536 collision; the failed flush poisoned the transaction.
            # ponytail: rollback drops the FOR UPDATE lock for the retry — a
            # same-user race here is bounded by the breaker, accepted.
            await db.rollback()
    raise ValueError("Could not allocate a unique slug")


async def list_user_workspaces(db: AsyncSession, user_id: uuid.UUID) -> list[Workspace]:
    stmt = (
        select(Workspace)
        .join(WorkspaceMembership)
        .where(WorkspaceMembership.user_id == user_id)
        .order_by(Workspace.created_at)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_workspace(db: AsyncSession, workspace_id: uuid.UUID) -> Workspace | None:
    return await db.get(Workspace, workspace_id)


async def update_workspace(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    name: str | None = None,
    description: str | None = None,
) -> Workspace:
    workspace = await db.get(Workspace, workspace_id)
    if not workspace:
        raise ValueError("Workspace not found")
    if name is not None:
        workspace.name = name
    if description is not None:
        workspace.description = description
    await db.commit()
    return workspace


async def delete_workspace(db: AsyncSession, workspace_id: uuid.UUID) -> None:
    workspace = await db.get(Workspace, workspace_id)
    if not workspace:
        raise ValueError("Workspace not found")
    await db.delete(workspace)
    await db.commit()


async def list_members(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    q: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    stmt = (
        select(WorkspaceMembership, User)
        .join(User, WorkspaceMembership.user_id == User.id)
        .where(WorkspaceMembership.workspace_id == workspace_id)
    )
    if q:
        # autoescape so %/_ in user input are treated literally, not as LIKE
        # wildcards (consistent with admin_service search).
        stmt = stmt.where(
            User.name.icontains(q, autoescape=True)
            | User.email.icontains(q, autoescape=True)
        )
    stmt = stmt.order_by(WorkspaceMembership.joined_at)
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    return [
        {
            "user_id": membership.user_id,
            "email": user.email,
            "name": user.name,
            "avatar_url": user.avatar_url,
            "role": membership.role,
            "joined_at": membership.joined_at,
        }
        for membership, user in result.all()
    ]


async def invite_member(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    email: str,
    role: str = "viewer",
    actor_role: str = "admin",
) -> WorkspaceMembership:
    # Only owners can grant the owner role
    if role == "owner" and actor_role != "owner":
        raise ValueError("Only workspace owners can grant the owner role")

    user = await db.execute(select(User).where(User.email == email))
    user = user.scalar_one_or_none()
    if not user:
        raise ValueError("User not found")

    existing = await db.execute(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user.id,
        )
    )
    if existing.scalar_one_or_none():
        raise ValueError("User is already a member of this workspace")

    await organization_service.assert_user_allowed_in_workspace(db, user, workspace_id)

    membership = WorkspaceMembership(
        workspace_id=workspace_id, user_id=user.id, role=role
    )
    db.add(membership)
    await db.commit()
    return membership


async def _count_owners(db: AsyncSession, workspace_id: uuid.UUID) -> int:
    # FOR UPDATE locks owner rows to prevent concurrent demotion/removal races.
    # Postgres forbids FOR UPDATE with aggregates, so lock the rows and count
    # them client-side instead of SELECT count(*).
    stmt = (
        select(WorkspaceMembership.user_id)
        .where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.role == "owner",
        )
        .with_for_update()
    )
    result = await db.execute(stmt)
    return len(result.scalars().all())


async def update_member_role(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str,
    actor_role: str = "admin",
) -> WorkspaceMembership:
    stmt = (
        select(WorkspaceMembership)
        .where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user_id,
        )
        .with_for_update()
    )
    result = await db.execute(stmt)
    membership = result.scalar_one_or_none()
    if not membership:
        raise ValueError("Membership not found")

    # Only owners can grant the owner role
    if role == "owner" and actor_role != "owner":
        raise ValueError("Only workspace owners can grant the owner role")

    # Prevent demoting the last owner
    if membership.role == "owner" and role != "owner":
        if await _count_owners(db, workspace_id) <= 1:
            raise ValueError("Cannot demote the last workspace owner")

    # Only owners can demote other owners
    if membership.role == "owner" and actor_role != "owner":
        raise ValueError("Only workspace owners can change another owner's role")

    old_role = membership.role
    membership.role = role
    await db.commit()
    # Revoke tokens so stale role claims can't be used
    if role != old_role:
        await token_service.revoke_all_user_tokens(str(user_id))
    return membership


async def remove_member(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    actor_role: str = "admin",
) -> None:
    stmt = (
        select(WorkspaceMembership)
        .where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user_id,
        )
        .with_for_update()
    )
    result = await db.execute(stmt)
    membership = result.scalar_one_or_none()
    if not membership:
        raise ValueError("Membership not found")

    # Prevent removing an owner unless actor is also an owner
    if membership.role == "owner" and actor_role != "owner":
        raise ValueError("Only workspace owners can remove another owner")

    # Prevent removing the last owner
    if membership.role == "owner":
        if await _count_owners(db, workspace_id) <= 1:
            raise ValueError("Cannot remove the last workspace owner")

    # Purge the user's workspace-scoped groups, RBAC roles, and entity-ACL
    # shares in the same transaction: none of these tables is FK-tied to
    # workspace_memberships, and neither check_action nor check_permission
    # re-joins it — stale rows would silently reinstate old privileges on
    # re-invite.
    await db.execute(
        delete(GroupMembership).where(
            GroupMembership.user_id == user_id,
            GroupMembership.group_id.in_(
                select(Group.id).where(Group.workspace_id == workspace_id)
            ),
        )
    )
    await db.execute(
        delete(UserRole).where(
            UserRole.user_id == user_id,
            UserRole.role_id.in_(
                select(Role.id).where(Role.workspace_id == workspace_id)
            ),
        )
    )
    await db.execute(
        delete(ResourceShare).where(
            ResourceShare.grantee_type == "user",
            ResourceShare.grantee_id == user_id,
            ResourceShare.resource_permission_id.in_(
                select(ResourcePermission.id).where(
                    ResourcePermission.workspace_id == workspace_id
                )
            ),
        )
    )
    await db.delete(membership)
    await db.commit()
    # Revoke tokens — user no longer belongs to this workspace
    await token_service.revoke_all_user_tokens(str(user_id))
