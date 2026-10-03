import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.group import Group, GroupMembership
from src.models.role import (
    ActionUsage,
    GroupRole,
    Role,
    RoleAction,
    ServiceAction,
    UserRole,
)
from src.models.user import User
from src.services import activity_service


async def register_actions(
    db: AsyncSession,
    service_name: str,
    actions: list[dict],
) -> list[ServiceAction]:
    """Idempotently register a service's RBAC actions.

    Uses a single atomic ``INSERT ... ON CONFLICT DO UPDATE`` keyed on the
    ``uq_service_action`` (service_name, action) constraint. The previous
    read-then-insert was non-atomic: it held the unique-index lock across the
    round-trip, so a slow/aborted registrant could strand the lock and stall
    every concurrent or retried registration (manifesting as client-side
    ``ReadTimeout`` at startup). The upsert closes that window. A ``NULL``
    incoming description never clears an existing one.
    """
    if not actions:
        return []

    # Collapse duplicate action names (last description wins). A single
    # INSERT ... ON CONFLICT DO UPDATE cannot touch the same target row twice
    # ("ON CONFLICT DO UPDATE command cannot affect row a second time"), so a
    # batch containing the same action twice must be deduped before the upsert.
    deduped: dict[str, dict] = {}
    for a in actions:
        deduped[a["action"]] = a

    rows = [
        {
            "id": uuid.uuid4(),
            "service_name": service_name,
            "action": a["action"],
            "description": a.get("description"),
        }
        for a in deduped.values()
    ]
    stmt = pg_insert(ServiceAction).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_service_action",
        set_={
            "description": func.coalesce(
                stmt.excluded.description, ServiceAction.description
            )
        },
    )
    await db.execute(stmt)
    await db.commit()

    # Read back deterministically. populate_existing refreshes any instances
    # already in this session's identity map (expire_on_commit=False), so the
    # returned descriptions reflect the just-committed values, not stale ones.
    result = await db.execute(
        select(ServiceAction)
        .where(
            ServiceAction.service_name == service_name,
            ServiceAction.action.in_(list(deduped)),
        )
        .order_by(ServiceAction.action)
        .execution_options(populate_existing=True)
    )
    return list(result.scalars().all())


def _granted_stmt(
    col,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    service_name: str,
    action: str | None = None,
):
    """UNION of the two grant paths: direct (user_roles) and via group
    (group_roles ⋈ group_memberships). UNION dedups, so a role granted both
    ways appears once. Group members are always workspace members
    (group_service guards + remove_member purge), so no membership re-join."""

    def _scoped(stmt):
        stmt = stmt.where(
            Role.workspace_id == workspace_id,
            ServiceAction.service_name == service_name,
        )
        if action is not None:
            stmt = stmt.where(ServiceAction.action == action)
        return stmt

    # select_from(Role) anchors the FROM clause on Role regardless of which
    # column is selected. Without it, selecting ServiceAction.action (as
    # get_user_actions does) makes SQLAlchemy infer FROM service_actions, and
    # the join to UserRole/GroupRole (whose ON-clause references Role.id
    # before Role is in the join graph) compiles but is rejected by Postgres
    # at execution time: "missing FROM-clause entry for table roles".
    direct = _scoped(
        select(col)
        .select_from(Role)
        .join(UserRole, UserRole.role_id == Role.id)
        .join(RoleAction, RoleAction.role_id == Role.id)
        .join(ServiceAction, RoleAction.service_action_id == ServiceAction.id)
        .where(UserRole.user_id == user_id)
    )
    via_group = _scoped(
        select(col)
        .select_from(Role)
        .join(GroupRole, GroupRole.role_id == Role.id)
        .join(GroupMembership, GroupMembership.group_id == GroupRole.group_id)
        .join(RoleAction, RoleAction.role_id == Role.id)
        .join(ServiceAction, RoleAction.service_action_id == ServiceAction.id)
        .where(GroupMembership.user_id == user_id)
    )
    return direct.union(via_group)


async def check_action(
    db: AsyncSession,
    user_id: uuid.UUID,
    service_name: str,
    action: str,
    workspace_id: uuid.UUID,
) -> tuple[bool, list[str]]:
    stmt = _granted_stmt(Role.name, user_id, workspace_id, service_name, action)
    result = await db.execute(stmt)
    roles = list(result.scalars().all())
    return (len(roles) > 0, roles)


async def record_action_check(
    db: AsyncSession,
    *,
    allowed: bool,
    user_id: uuid.UUID,
    service_name: str,
    action: str,
    workspace_id: uuid.UUID,
) -> None:
    """Record one check_action verdict. Caller commits (and must not let a
    recording failure break the check response — this is the SDK hot path).

    Allowed → +1 on the action_usage daily rollup (per-event rows would bloat).
    Denied → admin-visible ``action_denied`` activity event (rare, high signal).
    """
    if allowed:
        stmt = (
            pg_insert(ActionUsage)
            .values(
                day=datetime.now(UTC).date(),
                workspace_id=workspace_id,
                user_id=user_id,
                service_name=service_name,
                action=action,
                count=1,
            )
            .on_conflict_do_update(
                index_elements=[
                    "day",
                    "workspace_id",
                    "user_id",
                    "service_name",
                    "action",
                ],
                set_={"count": ActionUsage.count + 1},
            )
        )
        await db.execute(stmt)
    else:
        await activity_service.log_activity(
            db,
            action="action_denied",
            target_type="user",
            target_id=user_id,
            actor_id=user_id,
            workspace_id=workspace_id,
            detail={"service_name": service_name, "action": action},
        )


async def get_user_actions(
    db: AsyncSession,
    user_id: uuid.UUID,
    service_name: str,
    workspace_id: uuid.UUID,
) -> list[str]:
    stmt = _granted_stmt(ServiceAction.action, user_id, workspace_id, service_name)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def list_service_actions(
    db: AsyncSession,
    service_name: str | None = None,
) -> list[ServiceAction]:
    stmt = select(ServiceAction).order_by(
        ServiceAction.service_name, ServiceAction.action
    )
    if service_name:
        stmt = stmt.where(ServiceAction.service_name == service_name)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def delete_service_action(
    db: AsyncSession,
    service_action_id: uuid.UUID,
) -> bool:
    action = await db.get(ServiceAction, service_action_id)
    if not action:
        return False
    await db.delete(action)
    await db.commit()
    return True


async def create_role(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    name: str,
    description: str | None = None,
    created_by: uuid.UUID | None = None,
) -> Role:
    role = Role(
        workspace_id=workspace_id,
        name=name,
        description=description,
        created_by=created_by,
    )
    db.add(role)
    await db.commit()
    return role


async def update_role(
    db: AsyncSession,
    role_id: uuid.UUID,
    name: str | None = None,
    description: str | None = None,
) -> Role:
    role = await db.get(Role, role_id)
    if not role:
        raise ValueError("Role not found")
    if name is not None:
        role.name = name
    if description is not None:
        role.description = description
    await db.commit()
    return role


async def delete_role(db: AsyncSession, role_id: uuid.UUID) -> None:
    role = await db.get(Role, role_id)
    if not role:
        raise ValueError("Role not found")
    await db.delete(role)
    await db.commit()


async def list_workspace_roles(
    db: AsyncSession,
    workspace_id: uuid.UUID,
) -> list[dict]:
    action_count = (
        select(func.count(RoleAction.id))
        .where(RoleAction.role_id == Role.id)
        .correlate(Role)
        .scalar_subquery()
    )
    member_count = (
        select(func.count(UserRole.id))
        .where(UserRole.role_id == Role.id)
        .correlate(Role)
        .scalar_subquery()
    )
    group_count = (
        select(func.count(GroupRole.id))
        .where(GroupRole.role_id == Role.id)
        .correlate(Role)
        .scalar_subquery()
    )
    stmt = (
        select(
            Role,
            action_count.label("action_count"),
            member_count.label("member_count"),
            group_count.label("group_count"),
        )
        .where(Role.workspace_id == workspace_id)
        .order_by(Role.created_at)
    )
    result = await db.execute(stmt)
    return [
        {
            "id": role.id,
            "workspace_id": role.workspace_id,
            "name": role.name,
            "description": role.description,
            "created_by": role.created_by,
            "created_at": role.created_at,
            "action_count": ac,
            "member_count": mc,
            "group_count": gc,
        }
        for role, ac, mc, gc in result.all()
    ]


async def add_role_actions(
    db: AsyncSession,
    role_id: uuid.UUID,
    service_action_ids: list[uuid.UUID],
) -> None:
    role = await db.get(Role, role_id)
    if not role:
        raise ValueError("Role not found")
    for said in service_action_ids:
        action = await db.get(ServiceAction, said)
        if not action:
            raise ValueError(f"Service action {said} not found")
        ra = RoleAction(role_id=role_id, service_action_id=said)
        db.add(ra)
    await db.commit()


async def remove_role_action(
    db: AsyncSession,
    role_id: uuid.UUID,
    service_action_id: uuid.UUID,
) -> None:
    stmt = select(RoleAction).where(
        RoleAction.role_id == role_id,
        RoleAction.service_action_id == service_action_id,
    )
    result = await db.execute(stmt)
    ra = result.scalar_one_or_none()
    if not ra:
        raise ValueError("Role action not found")
    await db.delete(ra)
    await db.commit()


async def list_role_actions(
    db: AsyncSession,
    role_id: uuid.UUID,
) -> list[ServiceAction]:
    stmt = (
        select(ServiceAction)
        .join(RoleAction, RoleAction.service_action_id == ServiceAction.id)
        .where(RoleAction.role_id == role_id)
        .order_by(ServiceAction.service_name, ServiceAction.action)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def assign_user_role(
    db: AsyncSession,
    user_id: uuid.UUID,
    role_id: uuid.UUID,
    assigned_by: uuid.UUID | None = None,
    workspace_id: uuid.UUID | None = None,
) -> UserRole:
    role = await db.get(Role, role_id)
    if not role:
        raise ValueError("Role not found")
    if workspace_id is not None and role.workspace_id != workspace_id:
        raise ValueError("Role not found in this workspace")
    # Roles must only bind to current workspace members — check_action never
    # re-joins membership, so a role pre-assigned to a non-member would lie
    # dormant and silently go live if that user is ever invited.
    from src.models.workspace import WorkspaceMembership

    member_stmt = select(WorkspaceMembership).where(
        WorkspaceMembership.workspace_id == role.workspace_id,
        WorkspaceMembership.user_id == user_id,
    )
    if not (await db.execute(member_stmt)).scalar_one_or_none():
        raise ValueError("User is not a member of this workspace")
    ur = UserRole(user_id=user_id, role_id=role_id, assigned_by=assigned_by)
    db.add(ur)
    await db.commit()
    return ur


async def remove_user_role(
    db: AsyncSession,
    user_id: uuid.UUID,
    role_id: uuid.UUID,
) -> None:
    stmt = select(UserRole).where(
        UserRole.user_id == user_id,
        UserRole.role_id == role_id,
    )
    result = await db.execute(stmt)
    ur = result.scalar_one_or_none()
    if not ur:
        raise ValueError("User role not found")
    await db.delete(ur)
    await db.commit()


async def list_role_members(
    db: AsyncSession,
    role_id: uuid.UUID,
) -> list[dict]:
    stmt = (
        select(UserRole, User)
        .join(User, UserRole.user_id == User.id)
        .where(UserRole.role_id == role_id)
        .order_by(UserRole.assigned_at)
    )
    result = await db.execute(stmt)
    return [
        {
            "user_id": ur.user_id,
            "email": user.email,
            "name": user.name,
            "assigned_at": ur.assigned_at,
            "assigned_by": ur.assigned_by,
        }
        for ur, user in result.all()
    ]


async def assign_group_role(
    db: AsyncSession,
    group_id: uuid.UUID,
    role_id: uuid.UUID,
    assigned_by: uuid.UUID | None = None,
) -> GroupRole:
    role = await db.get(Role, role_id)
    if not role:
        raise ValueError("Role not found")
    group = await db.get(Group, group_id)
    if not group:
        raise ValueError("Group not found")
    # Groups are workspace-scoped and group members are guaranteed workspace
    # members (group_service.add_member guard + remove_member purge), so this
    # is the only scope check a group binding needs.
    if group.workspace_id != role.workspace_id:
        raise ValueError("Group and role belong to different workspaces")
    gr = GroupRole(group_id=group_id, role_id=role_id, assigned_by=assigned_by)
    db.add(gr)
    try:
        await db.commit()
    except IntegrityError:
        raise ValueError("Group is already assigned to this role") from None
    return gr


async def remove_group_role(
    db: AsyncSession,
    group_id: uuid.UUID,
    role_id: uuid.UUID,
) -> None:
    stmt = select(GroupRole).where(
        GroupRole.group_id == group_id,
        GroupRole.role_id == role_id,
    )
    result = await db.execute(stmt)
    gr = result.scalar_one_or_none()
    if not gr:
        raise ValueError("Group role not found")
    await db.delete(gr)
    await db.commit()


async def list_role_groups(
    db: AsyncSession,
    role_id: uuid.UUID,
) -> list[dict]:
    member_count = (
        select(func.count(GroupMembership.id))
        .where(GroupMembership.group_id == Group.id)
        .correlate(Group)
        .scalar_subquery()
    )
    stmt = (
        select(GroupRole, Group, member_count.label("member_count"))
        .join(Group, GroupRole.group_id == Group.id)
        .where(GroupRole.role_id == role_id)
        .order_by(GroupRole.assigned_at)
    )
    result = await db.execute(stmt)
    return [
        {
            "group_id": gr.group_id,
            "name": g.name,
            "description": g.description,
            "member_count": mc,
            "assigned_at": gr.assigned_at,
            "assigned_by": gr.assigned_by,
        }
        for gr, g, mc in result.all()
    ]
