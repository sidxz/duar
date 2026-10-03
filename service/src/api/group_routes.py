import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.middleware.rate_limit import limiter, user_or_ip_key

from src.api.dependencies import (
    CurrentUser,
    get_current_user,
    get_current_user_flexible,
)
from src.database import get_db
from src.schemas.group import (
    GroupCreateRequest,
    GroupMemberResponse,
    GroupResponse,
    GroupUpdateRequest,
)
from src.services import activity_service, group_service

router = APIRouter(prefix="/workspaces/{workspace_id}/groups", tags=["groups"])


def _require_workspace_match(user: CurrentUser, workspace_id: uuid.UUID) -> None:
    if user.workspace_id != workspace_id:
        raise HTTPException(status_code=403, detail="Not a member of this workspace")


def _require_role(user: CurrentUser, minimum: str) -> None:
    hierarchy = {"viewer": 0, "editor": 1, "admin": 2, "owner": 3}
    if hierarchy.get(user.workspace_role, -1) < hierarchy[minimum]:
        raise HTTPException(status_code=403, detail="Insufficient role")


def _to_http(e: ValueError) -> HTTPException:
    """Map service-layer ValueErrors to 404 (missing) or 400 (invalid)."""
    detail = str(e)
    status = 404 if "not found" in detail.lower() else 400
    return HTTPException(status_code=status, detail=detail)


@router.post("", response_model=GroupResponse, status_code=201)
async def create_group(
    workspace_id: uuid.UUID,
    body: GroupCreateRequest,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_workspace_match(user, workspace_id)
    _require_role(user, "admin")
    try:
        group = await group_service.create_group(
            db,
            workspace_id=workspace_id,
            name=body.name,
            created_by=user.user_id,
            description=body.description,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    await activity_service.log_activity(
        db,
        action="group_created",
        target_type="group",
        target_id=group.id,
        actor_id=user.user_id,
        workspace_id=workspace_id,
    )
    await db.commit()
    return group


@router.get("", response_model=list[GroupResponse])
async def list_groups(
    workspace_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user_flexible),
    db: AsyncSession = Depends(get_db),
):
    _require_workspace_match(user, workspace_id)
    return await group_service.list_groups(db, workspace_id)


@router.patch("/{group_id}", response_model=GroupResponse)
async def update_group(
    workspace_id: uuid.UUID,
    group_id: uuid.UUID,
    body: GroupUpdateRequest,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_workspace_match(user, workspace_id)
    _require_role(user, "admin")
    try:
        group = await group_service.update_group(
            db, group_id, workspace_id, name=body.name, description=body.description
        )
    except ValueError as e:
        raise _to_http(e)
    await activity_service.log_activity(
        db,
        action="group_updated",
        target_type="group",
        target_id=group_id,
        actor_id=user.user_id,
        workspace_id=workspace_id,
    )
    await db.commit()
    return group


@router.delete("/{group_id}", status_code=204)
async def delete_group(
    workspace_id: uuid.UUID,
    group_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_workspace_match(user, workspace_id)
    _require_role(user, "admin")
    try:
        await group_service.delete_group(db, group_id, workspace_id)
    except ValueError as e:
        raise _to_http(e)
    await activity_service.log_activity(
        db,
        action="group_deleted",
        target_type="group",
        target_id=group_id,
        actor_id=user.user_id,
        workspace_id=workspace_id,
    )
    await db.commit()


@router.get("/{group_id}/members", response_model=list[GroupMemberResponse])
@limiter.limit(settings.rate_limit_read, key_func=user_or_ip_key)
async def list_group_members(
    request: Request,
    workspace_id: uuid.UUID,
    group_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user_flexible),
    db: AsyncSession = Depends(get_db),
):
    _require_workspace_match(user, workspace_id)
    # Verify group belongs to this workspace (prevents cross-workspace enumeration)
    from src.services.group_service import _get_group_in_workspace

    try:
        await _get_group_in_workspace(db, group_id, workspace_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Group not found")
    return await group_service.list_group_members(db, group_id)


@router.post("/{group_id}/members/{member_user_id}", status_code=201)
async def add_group_member(
    workspace_id: uuid.UUID,
    group_id: uuid.UUID,
    member_user_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_workspace_match(user, workspace_id)
    _require_role(user, "admin")
    try:
        await group_service.add_member(db, group_id, workspace_id, member_user_id)
    except ValueError as e:
        raise _to_http(e)
    await activity_service.log_activity(
        db,
        action="group_member_added",
        target_type="user",
        target_id=member_user_id,
        actor_id=user.user_id,
        workspace_id=workspace_id,
        detail={"group_id": str(group_id)},
    )
    await db.commit()
    return {"status": "ok"}


@router.delete("/{group_id}/members/{member_user_id}", status_code=204)
async def remove_group_member(
    workspace_id: uuid.UUID,
    group_id: uuid.UUID,
    member_user_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_workspace_match(user, workspace_id)
    _require_role(user, "admin")
    try:
        await group_service.remove_member(db, group_id, workspace_id, member_user_id)
    except ValueError as e:
        raise _to_http(e)
    await activity_service.log_activity(
        db,
        action="group_member_removed",
        target_type="user",
        target_id=member_user_id,
        actor_id=user.user_id,
        workspace_id=workspace_id,
        detail={"group_id": str(group_id)},
    )
    await db.commit()
