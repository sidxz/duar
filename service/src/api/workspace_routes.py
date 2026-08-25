import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.middleware.rate_limit import limiter, user_or_ip_key

from src.api.dependencies import (
    CurrentUser,
    get_current_user,
    get_current_user_flexible,
)
from src.database import get_db
from src.schemas.workspace import (
    InviteMemberRequest,
    UpdateMemberRoleRequest,
    WorkspaceCreateRequest,
    WorkspaceMemberResponse,
    WorkspaceResponse,
    WorkspaceUpdateRequest,
)
from src.services import activity_service, workspace_service

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def _require_workspace_match(user: CurrentUser, workspace_id: uuid.UUID) -> None:
    """Verify JWT workspace matches path workspace."""
    if user.workspace_id != workspace_id:
        raise HTTPException(status_code=403, detail="Not a member of this workspace")


def _require_role(user: CurrentUser, minimum: str) -> None:
    """Enforce minimum workspace role from JWT."""
    hierarchy = {"viewer": 0, "editor": 1, "admin": 2, "owner": 3}
    if hierarchy.get(user.workspace_role, -1) < hierarchy[minimum]:
        raise HTTPException(status_code=403, detail="Insufficient role")


@router.post("", response_model=WorkspaceResponse, status_code=201)
async def create_workspace(
    body: WorkspaceCreateRequest,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not settings.self_serve_enabled:
        raise HTTPException(
            status_code=403, detail="Workspace creation is disabled on this server"
        )
    try:
        workspace = await workspace_service.create_workspace(
            db,
            name=body.name,
            slug=body.slug,
            created_by=user.user_id,
            description=body.description,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    await activity_service.log_activity(
        db,
        action="workspace_created",
        target_type="workspace",
        target_id=workspace.id,
        actor_id=user.user_id,
        workspace_id=workspace.id,
        detail={"name": workspace.name, "slug": workspace.slug},
    )
    await db.commit()
    return workspace


@router.get("", response_model=list[WorkspaceResponse])
async def list_workspaces(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await workspace_service.list_user_workspaces(db, user.user_id)


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_workspace_match(user, workspace_id)
    workspace = await workspace_service.get_workspace(db, workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return workspace


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    workspace_id: uuid.UUID,
    body: WorkspaceUpdateRequest,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_workspace_match(user, workspace_id)
    _require_role(user, "admin")
    try:
        workspace = await workspace_service.update_workspace(
            db, workspace_id, name=body.name, description=body.description
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    await activity_service.log_activity(
        db,
        action="workspace_updated",
        target_type="workspace",
        target_id=workspace_id,
        actor_id=user.user_id,
        workspace_id=workspace_id,
    )
    await db.commit()
    return workspace


@router.delete("/{workspace_id}", status_code=204)
async def delete_workspace(
    workspace_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_workspace_match(user, workspace_id)
    _require_role(user, "owner")
    workspace = await workspace_service.get_workspace(db, workspace_id)
    await workspace_service.delete_workspace(db, workspace_id)
    if workspace:
        await activity_service.log_activity(
            db,
            action="workspace_deleted",
            target_type="workspace",
            target_id=workspace_id,
            actor_id=user.user_id,
            detail={"name": workspace.name, "slug": workspace.slug},
        )
        await db.commit()


# --- Members ---


@router.get("/{workspace_id}/members", response_model=list[WorkspaceMemberResponse])
@limiter.limit(settings.rate_limit_read, key_func=user_or_ip_key)
async def list_members(
    request: Request,
    workspace_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user_flexible),
    db: AsyncSession = Depends(get_db),
    q: str | None = Query(default=None, max_length=100),
    limit: int | None = None,
):
    _require_workspace_match(user, workspace_id)
    if limit is not None:
        limit = min(max(limit, 1), 50)
    return await workspace_service.list_members(db, workspace_id, q=q, limit=limit)


@router.post(
    "/{workspace_id}/members/invite",
    response_model=WorkspaceMemberResponse,
    status_code=201,
)
async def invite_member(
    workspace_id: uuid.UUID,
    body: InviteMemberRequest,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if settings.self_serve_enabled:
        # Consent rule (spec): in self-serve mode nobody is added to a workspace
        # without their own action, and this endpoint is an email-existence oracle.
        raise HTTPException(
            status_code=403,
            detail="Direct member add is disabled in self-serve mode; use invitations",
        )
    _require_workspace_match(user, workspace_id)
    _require_role(user, "admin")
    try:
        membership = await workspace_service.invite_member(
            db,
            workspace_id,
            email=body.email,
            role=body.role,
            actor_role=user.workspace_role,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await activity_service.log_activity(
        db,
        action="member_invited",
        target_type="user",
        target_id=membership.user_id,
        actor_id=user.user_id,
        workspace_id=workspace_id,
        detail={"email": body.email, "role": body.role},
    )
    await db.commit()
    return membership


@router.patch("/{workspace_id}/members/{user_id}")
async def update_member_role(
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    body: UpdateMemberRoleRequest,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_workspace_match(user, workspace_id)
    _require_role(user, "admin")
    try:
        result = await workspace_service.update_member_role(
            db, workspace_id, user_id, role=body.role, actor_role=user.workspace_role
        )
    except ValueError as e:
        detail = str(e)
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=403, detail=detail)
    await activity_service.log_activity(
        db,
        action="member_role_changed",
        target_type="user",
        target_id=user_id,
        actor_id=user.user_id,
        workspace_id=workspace_id,
        detail={"role": body.role},
    )
    await db.commit()
    return result


@router.delete("/{workspace_id}/members/{user_id}", status_code=204)
async def remove_member(
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_workspace_match(user, workspace_id)
    _require_role(user, "admin")
    try:
        await workspace_service.remove_member(
            db, workspace_id, user_id, actor_role=user.workspace_role
        )
    except ValueError as e:
        detail = str(e)
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=403, detail=detail)
    await activity_service.log_activity(
        db,
        action="member_removed",
        target_type="user",
        target_id=user_id,
        actor_id=user.user_id,
        workspace_id=workspace_id,
    )
    await db.commit()
