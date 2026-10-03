import uuid

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.group import Group, GroupMembership
from src.models.permission import ResourceShare
from src.models.user import User
from src.services import token_service


async def create_group(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    name: str,
    created_by: uuid.UUID,
    description: str | None = None,
) -> Group:
    group = Group(
        workspace_id=workspace_id,
        name=name,
        description=description,
        created_by=created_by,
    )
    db.add(group)
    try:
        await db.commit()
    except IntegrityError:
        raise ValueError(
            "A group with this name already exists in this workspace"
        ) from None
    return group


async def list_groups(db: AsyncSession, workspace_id: uuid.UUID) -> list[Group]:
    stmt = (
        select(Group)
        .where(Group.workspace_id == workspace_id)
        .order_by(Group.created_at)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _get_group_in_workspace(
    db: AsyncSession, group_id: uuid.UUID, workspace_id: uuid.UUID
) -> Group:
    """Load a group and verify it belongs to the expected workspace."""
    group = await db.get(Group, group_id)
    if not group or group.workspace_id != workspace_id:
        raise ValueError("Group not found")
    return group


async def update_group(
    db: AsyncSession,
    group_id: uuid.UUID,
    workspace_id: uuid.UUID,
    name: str | None = None,
    description: str | None = None,
) -> Group:
    group = await _get_group_in_workspace(db, group_id, workspace_id)
    if name is not None:
        group.name = name
    if description is not None:
        group.description = description
    await db.commit()
    return group


async def delete_group(
    db: AsyncSession, group_id: uuid.UUID, workspace_id: uuid.UUID
) -> None:
    group = await _get_group_in_workspace(db, group_id, workspace_id)
    member_ids = list(
        (
            await db.execute(
                select(GroupMembership.user_id).where(
                    GroupMembership.group_id == group_id
                )
            )
        )
        .scalars()
        .all()
    )
    # ResourceShare has no FK to groups — purge the group's shares explicitly
    # or they orphan permanently.
    await db.execute(
        delete(ResourceShare).where(
            ResourceShare.grantee_type == "group",
            ResourceShare.grantee_id == group_id,
        )
    )
    await db.delete(group)
    await db.commit()
    # Revoke members' tokens so stale group claims can't be used
    for uid in member_ids:
        await token_service.revoke_all_user_tokens(str(uid))


async def add_member(
    db: AsyncSession,
    group_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> GroupMembership:
    await _get_group_in_workspace(db, group_id, workspace_id)
    # Verify user is a member of this workspace
    from src.models.workspace import WorkspaceMembership

    ws_stmt = select(WorkspaceMembership).where(
        WorkspaceMembership.workspace_id == workspace_id,
        WorkspaceMembership.user_id == user_id,
    )
    ws_result = await db.execute(ws_stmt)
    if not ws_result.scalar_one_or_none():
        raise ValueError("User is not a member of this workspace")
    membership = GroupMembership(group_id=group_id, user_id=user_id)
    db.add(membership)
    try:
        await db.commit()
    except IntegrityError:
        raise ValueError("User is already a member of this group") from None
    return membership


async def remove_member(
    db: AsyncSession,
    group_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    await _get_group_in_workspace(db, group_id, workspace_id)
    stmt = select(GroupMembership).where(
        GroupMembership.group_id == group_id,
        GroupMembership.user_id == user_id,
    )
    result = await db.execute(stmt)
    membership = result.scalar_one_or_none()
    if not membership:
        raise ValueError("Group membership not found")
    await db.delete(membership)
    await db.commit()
    # Revoke tokens so stale group claims can't be used
    await token_service.revoke_all_user_tokens(str(user_id))


async def list_group_members(db: AsyncSession, group_id: uuid.UUID) -> list[dict]:
    stmt = (
        select(GroupMembership, User)
        .join(User, GroupMembership.user_id == User.id)
        .where(GroupMembership.group_id == group_id)
        .order_by(GroupMembership.added_at)
    )
    result = await db.execute(stmt)
    return [
        {
            "user_id": membership.user_id,
            "email": user.email,
            "name": user.name,
            "added_at": membership.added_at,
        }
        for membership, user in result.all()
    ]
