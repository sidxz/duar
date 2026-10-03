import csv
import io
import time
import uuid
from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import require_admin
from src.config import settings
from src.database import get_db
from src.version import __version__
from src.models.client_app import ClientApp
from src.models.group import Group
from src.models.user import User
from src.models.workspace import Workspace, WorkspaceMembership
from src.schemas.admin import (
    AdminAddUserToWorkspaceRequest,
    AdminResourcePermissionResponse,
    AdminStatsResponse,
    AdminUserDetailResponse,
    AdminUserUpdateRequest,
    AdminWorkspaceCreateRequest,
    AdminWorkspaceDetailResponse,
    BulkUserStatusRequest,
    CsvImportPreview,
    CsvImportResult,
    HealthCheckDetail,
    PaginatedResponse,
    SystemHealthResponse,
    SystemSettingsResponse,
    WorkspaceOption,
)
from src.schemas.workspace import (
    InviteMemberRequest,
    UpdateMemberRoleRequest,
    WorkspaceMemberResponse,
    WorkspaceUpdateRequest,
)
from src.schemas.group import (
    GroupCreateRequest,
    GroupUpdateRequest,
    GroupResponse,
    GroupMemberResponse,
)
from src.schemas.client_app import (
    ClientAppCreateRequest,
    ClientAppResponse,
    ClientAppUpdateRequest,
)
from src.schemas.service_app import (
    ServiceAppCreateRequest,
    ServiceAppCreateResponse,
    ServiceAppResponse,
    ServiceAppUpdateRequest,
)
from src.schemas.permission import ShareRequest, UpdateVisibilityRequest
from src.schemas.role import (
    AddRoleActionsRequest,
    RoleCreateRequest,
    RoleGroupResponse,
    RoleMemberResponse,
    RoleResponse,
    RoleUpdateRequest,
    ServiceActionResponse,
)
from src.schemas.realm import (
    RealmCreateRequest,
    RealmMemberResponse,
    RealmResponse,
    RealmUpdateRequest,
)
from src.services import (
    actions_insights_service,
    admin_service,
    activity_service,
    insights_service,
    workspace_service,
    group_service,
    permission_service,
    realm_service,
    role_service,
    service_app_service,
)
from src.middleware.cors import refresh_origins
from src.middleware.rate_limit import limiter, rate_limit_report, user_or_ip_key
from src.services import token_service

logger = structlog.get_logger()

router = APIRouter(
    prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)]
)


# ── Stats & Dashboard ────────────────────────────────────────────────


@router.get("/stats", response_model=AdminStatsResponse)
async def get_stats(db: AsyncSession = Depends(get_db)):
    return await admin_service.get_stats(db)


@router.get("/activity/summary")
async def get_activity_summary(
    days: int = Query(30, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
):
    return {"days": days, "items": await activity_service.daily_counts(db, days)}


@router.get("/activity/insights")
async def get_activity_insights(
    days: int = Query(30, ge=1, le=90),
    user_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    return await insights_service.signin_insights(db, days=days, actor_id=user_id)


@router.get("/actions/insights")
async def get_actions_insights(
    days: int = Query(30),
    workspace_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    if days not in (7, 30, 90):
        raise HTTPException(status_code=422, detail="days must be 7, 30, or 90")
    return await actions_insights_service.actions_insights(
        db, days=days, workspace_id=workspace_id
    )


@router.get("/activity", response_model=PaginatedResponse)
async def get_activity(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    action: str | None = Query(None),
    target_type: str | None = Query(None),
    workspace_id: uuid.UUID | None = Query(None),
    actor_id: uuid.UUID | None = Query(None),
    from_date: datetime | None = Query(None),
    to_date: datetime | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    return await activity_service.list_paginated(
        db,
        page=page,
        page_size=page_size,
        action=action,
        target_type=target_type,
        workspace_id=workspace_id,
        actor_id=actor_id,
        from_date=from_date,
        to_date=to_date,
    )


@router.get("/workspaces/all", response_model=list[WorkspaceOption])
async def list_all_workspaces(db: AsyncSession = Depends(get_db)):
    return await admin_service.list_all_workspaces(db)


# ── System ────────────────────────────────────────────────────────────


@router.get("/system/health", response_model=SystemHealthResponse)
async def system_health(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    checks = {}

    # DB check
    try:
        t0 = time.time()
        await db.execute(text("SELECT 1"))
        checks["database"] = HealthCheckDetail(
            status="ok", latency_ms=round((time.time() - t0) * 1000, 1)
        )
    except Exception as e:
        checks["database"] = HealthCheckDetail(
            status="error", latency_ms=0, error=str(e)
        )

    # Redis check
    try:
        r = await token_service.get_redis()
        t0 = time.time()
        await r.ping()
        checks["redis"] = HealthCheckDetail(
            status="ok", latency_ms=round((time.time() - t0) * 1000, 1)
        )
    except Exception as e:
        checks["redis"] = HealthCheckDetail(status="error", latency_ms=0, error=str(e))

    all_ok = all(c.status == "ok" for c in checks.values())
    start_time = getattr(request.app.state, "start_time", time.time())
    return SystemHealthResponse(
        status="healthy" if all_ok else "degraded",
        checks=checks,
        uptime_seconds=round(time.time() - start_time, 1),
        version=__version__,
    )


@router.get("/system/settings", response_model=SystemSettingsResponse)
async def system_settings(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    # OAuth providers
    providers = [
        {"name": "google", "configured": bool(settings.google_client_id)},
        {"name": "github", "configured": bool(settings.github_client_id)},
        # Identifier, not a label — must match the provider name used in the
        # OAuth routes (/auth/login/{provider}) and the SDK's `provider` field.
        {"name": "entra_id", "configured": bool(settings.entra_client_id)},
    ]

    # JWT info
    public_key_preview = ""
    try:
        public_key_preview = settings.jwt_public_key_path.read_text()[:80]
    except Exception:
        pass

    denylist_count = 0
    try:
        r = await token_service.get_redis()
        cursor, keys = await r.scan(0, match="bl:*", count=1000)
        denylist_count = len(keys)
    except Exception:
        pass

    jwt_info = {
        "algorithm": settings.jwt_algorithm,
        "access_token_expire_minutes": settings.access_token_expire_minutes,
        "refresh_token_expire_days": settings.refresh_token_expire_days,
        "public_key_preview": public_key_preview,
        "denylist_count": denylist_count,
    }

    # Security
    security = {
        "cookie_secure": settings.cookie_secure,
        "allowed_hosts": settings.allowed_hosts_list,
        "cors_origins": settings.cors_origin_list,
        "session_secret_configured": settings.session_secret_key
        != "dev-only-change-me-in-production",
        "admin_emails": settings.admin_email_list,
    }

    # Rate limits — reported live from config (see middleware/rate_limit.py).
    rate_limits = rate_limit_report()

    # Service keys (from DB)
    service_keys = []
    apps = await service_app_service.list_service_apps(db)
    for svc_app in apps:
        service_keys.append(
            {
                "name": svc_app.service_name,
                "preview": svc_app.key_prefix,
            }
        )

    service_info = {
        "base_url": settings.base_url,
        "frontend_url": settings.frontend_url,
        "admin_url": settings.admin_url,
    }

    self_serve = {
        "enabled": settings.self_serve_enabled,
        "max_workspaces_per_user": settings.self_serve_max_workspaces_per_user,
        "max_creates_per_hour": settings.self_serve_max_creates_per_hour,
    }

    await activity_service.log_activity(
        db,
        action="view_system_settings",
        target_type="system",
        target_id=uuid.UUID(int=0),
        actor_id=uuid.UUID(admin["sub"]),
    )
    await db.commit()

    return SystemSettingsResponse(
        oauth_providers=providers,
        jwt=jwt_info,
        security=security,
        rate_limits=rate_limits,
        service_keys=service_keys,
        service=service_info,
        self_serve=self_serve,
    )


# ── Users ─────────────────────────────────────────────────────────────


@router.post("/users/bulk-status")
@limiter.limit(settings.rate_limit_sensitive, key_func=user_or_ip_key)
async def bulk_user_status(
    request: Request,
    body: BulkUserStatusRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    affected = await admin_service.bulk_update_status(db, body.user_ids, body.is_active)
    await activity_service.log_activity(
        db,
        action="bulk_status_change",
        target_type="user",
        target_id=uuid.uuid4(),
        actor_id=uuid.UUID(admin["sub"]),
        detail={"user_count": len(body.user_ids), "is_active": body.is_active},
    )
    await db.commit()
    return {"status": "ok", "affected": affected}


@router.get("/users", response_model=PaginatedResponse)
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    return await admin_service.list_users(
        db, page=page, page_size=page_size, search=search
    )


@router.get("/users/{user_id}", response_model=AdminUserDetailResponse)
async def get_user_detail(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    user = await admin_service.get_user_detail(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.patch("/users/{user_id}", response_model=AdminUserDetailResponse)
async def update_user(
    user_id: uuid.UUID,
    body: AdminUserUpdateRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    user, actions = await admin_service.update_user(
        db,
        user_id,
        name=body.name,
        is_active=body.is_active,
        is_admin=body.is_admin,
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # One transaction for mutation + audit rows: a promote/demote can never
    # commit while its audit record is silently dropped. One row per actual
    # change (a combined is_admin+is_active request logs both).
    for action in actions:
        await activity_service.log_activity(
            db,
            action=action,
            target_type="user",
            target_id=user_id,
            actor_id=uuid.UUID(admin["sub"]),
        )
    await db.commit()

    return await admin_service.get_user_detail(db, user_id)


@router.post("/users/{user_id}/workspaces", status_code=201)
async def add_user_to_workspace(
    user_id: uuid.UUID,
    body: AdminAddUserToWorkspaceRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        await admin_service.add_user_to_workspace(
            db,
            user_id,
            body.workspace_id,
            body.role,
            actor_id=uuid.UUID(admin["sub"]),
        )
    except ValueError as e:
        # User-not-found / org-not-permitted — surface the reason to the admin.
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("app.error.unhandled", category="app", error=str(e), exc_info=True)
        raise HTTPException(status_code=400, detail="Failed to add user to workspace")
    return {"status": "ok"}


@router.post("/users/{user_id}/revoke-tokens")
@limiter.limit(settings.rate_limit_admin_write, key_func=user_or_ip_key)
async def revoke_user_tokens(
    request: Request,
    user_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    count = await token_service.revoke_all_user_tokens(str(user_id))
    await activity_service.log_activity(
        db,
        action="tokens_revoked",
        target_type="user",
        target_id=user_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"tokens_revoked": count},
    )
    await db.commit()
    return {"status": "ok", "tokens_revoked": count}


# ── Workspaces ────────────────────────────────────────────────────────


@router.get("/workspaces", response_model=PaginatedResponse)
async def list_workspaces(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    return await admin_service.list_workspaces(
        db, page=page, page_size=page_size, search=search
    )


@router.post(
    "/workspaces", response_model=AdminWorkspaceDetailResponse, status_code=201
)
async def create_workspace(
    body: AdminWorkspaceCreateRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    actor_id = uuid.UUID(admin["sub"])
    try:
        ws = await workspace_service.create_workspace(
            db,
            name=body.name,
            slug=body.slug,
            created_by=actor_id,
            description=body.description,
        )
    except Exception as e:
        logger.error("app.error.unhandled", category="app", error=str(e), exc_info=True)
        raise HTTPException(status_code=400, detail="Failed to create workspace")

    await activity_service.log_activity(
        db,
        action="workspace_created",
        target_type="workspace",
        target_id=ws.id,
        actor_id=actor_id,
        workspace_id=ws.id,
        detail={"name": ws.name, "slug": ws.slug},
    )
    await db.commit()
    return await admin_service.get_workspace_detail(db, ws.id)


@router.get("/workspaces/{workspace_id}", response_model=AdminWorkspaceDetailResponse)
async def get_workspace_detail(
    workspace_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    detail = await admin_service.get_workspace_detail(db, workspace_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return detail


@router.patch("/workspaces/{workspace_id}", response_model=AdminWorkspaceDetailResponse)
async def update_workspace(
    workspace_id: uuid.UUID,
    body: WorkspaceUpdateRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        await workspace_service.update_workspace(
            db, workspace_id, name=body.name, description=body.description
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    await activity_service.log_activity(
        db,
        action="workspace_updated",
        target_type="workspace",
        target_id=workspace_id,
        actor_id=uuid.UUID(admin["sub"]),
        workspace_id=workspace_id,
    )
    await db.commit()
    return await admin_service.get_workspace_detail(db, workspace_id)


@router.delete("/workspaces/{workspace_id}", status_code=204)
async def delete_workspace(
    workspace_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    detail = await admin_service.get_workspace_detail(db, workspace_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Workspace not found")
    try:
        await workspace_service.delete_workspace(db, workspace_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    await activity_service.log_activity(
        db,
        action="workspace_deleted",
        target_type="workspace",
        target_id=workspace_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"name": detail["name"], "slug": detail["slug"]},
    )
    await db.commit()


# ── Workspace Members ─────────────────────────────────────────────────


@router.get(
    "/workspaces/{workspace_id}/members", response_model=list[WorkspaceMemberResponse]
)
async def list_workspace_members(
    workspace_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    return await workspace_service.list_members(db, workspace_id)


@router.post("/workspaces/{workspace_id}/members/invite", status_code=201)
async def invite_workspace_member(
    workspace_id: uuid.UUID,
    body: InviteMemberRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        m = await workspace_service.invite_member(
            db, workspace_id, body.email, body.role, actor_role="owner"
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await activity_service.log_activity(
        db,
        action="member_invited",
        target_type="user",
        target_id=m.user_id,
        actor_id=uuid.UUID(admin["sub"]),
        workspace_id=workspace_id,
        detail={"email": body.email, "role": body.role},
    )
    await db.commit()
    return {"status": "ok"}


@router.patch("/workspaces/{workspace_id}/members/{user_id}")
async def update_workspace_member_role(
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    body: UpdateMemberRoleRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        await workspace_service.update_member_role(
            db, workspace_id, user_id, body.role, actor_role="owner"
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    await activity_service.log_activity(
        db,
        action="member_role_changed",
        target_type="user",
        target_id=user_id,
        actor_id=uuid.UUID(admin["sub"]),
        workspace_id=workspace_id,
        detail={"role": body.role},
    )
    await db.commit()
    return {"status": "ok"}


@router.delete("/workspaces/{workspace_id}/members/{user_id}", status_code=204)
async def remove_workspace_member(
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        await workspace_service.remove_member(
            db, workspace_id, user_id, actor_role="owner"
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    await activity_service.log_activity(
        db,
        action="member_removed",
        target_type="user",
        target_id=user_id,
        actor_id=uuid.UUID(admin["sub"]),
        workspace_id=workspace_id,
    )
    await db.commit()


# ── Groups ────────────────────────────────────────────────────────────


@router.get("/workspaces/{workspace_id}/groups", response_model=list[GroupResponse])
async def list_workspace_groups(
    workspace_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    return await group_service.list_groups(db, workspace_id)


@router.post(
    "/workspaces/{workspace_id}/groups", response_model=GroupResponse, status_code=201
)
async def create_group(
    workspace_id: uuid.UUID,
    body: GroupCreateRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    actor_id = uuid.UUID(admin["sub"])
    group = await group_service.create_group(
        db, workspace_id, body.name, actor_id, body.description
    )
    await activity_service.log_activity(
        db,
        action="group_created",
        target_type="group",
        target_id=group.id,
        actor_id=actor_id,
        workspace_id=workspace_id,
        detail={"name": group.name},
    )
    await db.commit()
    return group


@router.patch("/groups/{group_id}", response_model=GroupResponse)
async def update_group(
    group_id: uuid.UUID,
    body: GroupUpdateRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    grp = await db.get(Group, group_id)
    if not grp:
        raise HTTPException(status_code=404, detail="Group not found")
    try:
        group = await group_service.update_group(
            db, group_id, grp.workspace_id, name=body.name, description=body.description
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    await activity_service.log_activity(
        db,
        action="group_updated",
        target_type="group",
        target_id=group_id,
        actor_id=uuid.UUID(admin["sub"]),
    )
    await db.commit()
    return group


@router.delete("/groups/{group_id}", status_code=204)
async def delete_group(
    group_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    grp = await db.get(Group, group_id)
    if not grp:
        raise HTTPException(status_code=404, detail="Group not found")
    try:
        await group_service.delete_group(db, group_id, grp.workspace_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    await activity_service.log_activity(
        db,
        action="group_deleted",
        target_type="group",
        target_id=group_id,
        actor_id=uuid.UUID(admin["sub"]),
    )
    await db.commit()


@router.get("/groups/{group_id}/members", response_model=list[GroupMemberResponse])
async def list_group_members(
    group_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    return await group_service.list_group_members(db, group_id)


@router.post("/groups/{group_id}/members/{user_id}", status_code=201)
async def add_group_member(
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    grp = await db.get(Group, group_id)
    if not grp:
        raise HTTPException(status_code=404, detail="Group not found")
    try:
        await group_service.add_member(db, group_id, grp.workspace_id, user_id)
    except Exception as e:
        logger.error("app.error.unhandled", category="app", error=str(e), exc_info=True)
        raise HTTPException(status_code=400, detail="Failed to add group member")

    await activity_service.log_activity(
        db,
        action="group_member_added",
        target_type="user",
        target_id=user_id,
        actor_id=uuid.UUID(admin["sub"]),
    )
    await db.commit()
    return {"status": "ok"}


@router.delete("/groups/{group_id}/members/{user_id}", status_code=204)
async def remove_group_member(
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    grp = await db.get(Group, group_id)
    if not grp:
        raise HTTPException(status_code=404, detail="Group not found")
    try:
        await group_service.remove_member(db, group_id, grp.workspace_id, user_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    await activity_service.log_activity(
        db,
        action="group_member_removed",
        target_type="user",
        target_id=user_id,
        actor_id=uuid.UUID(admin["sub"]),
    )
    await db.commit()


# ── Permissions ───────────────────────────────────────────────────────


@router.get("/permissions", response_model=PaginatedResponse)
async def list_permissions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    workspace_id: uuid.UUID | None = Query(None),
    service_name: str | None = Query(None),
    resource_id: str | None = Query(None),
    owner: str | None = Query(None),
    sort_by: str | None = Query(None, pattern=r"^(shares|created_at)$"),
    sort_order: str = Query("desc", pattern=r"^(asc|desc)$"),
    db: AsyncSession = Depends(get_db),
):
    return await admin_service.list_permissions(
        db,
        page=page,
        page_size=page_size,
        workspace_id=workspace_id,
        service_name=service_name,
        resource_id=resource_id,
        owner=owner,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.get(
    "/permissions/{permission_id}", response_model=AdminResourcePermissionResponse
)
async def get_permission_detail(
    permission_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    detail = await admin_service.get_permission_detail(db, permission_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Permission not found")
    return detail


@router.patch("/permissions/{permission_id}/visibility")
async def update_permission_visibility(
    permission_id: uuid.UUID,
    body: UpdateVisibilityRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        perm = await permission_service.update_visibility(
            db, permission_id, body.visibility
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    await activity_service.log_activity(
        db,
        action="permission_visibility_changed",
        target_type="resource_permission",
        target_id=permission_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"visibility": body.visibility},
    )
    await db.commit()
    return {"status": "ok", "visibility": perm.visibility}


@router.post("/permissions/{permission_id}/share", status_code=201)
async def share_permission(
    permission_id: uuid.UUID,
    body: ShareRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    # Validate grantee belongs to the same workspace as the resource
    perm = await permission_service.get_permission_by_id(db, permission_id)
    if not perm:
        raise HTTPException(status_code=404, detail="Permission not found")
    if body.grantee_type == "user":
        from src.models.workspace import WorkspaceMembership

        stmt = select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == perm.workspace_id,
            WorkspaceMembership.user_id == body.grantee_id,
        )
        result = await db.execute(stmt)
        if not result.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail="Grantee user is not a member of the resource's workspace",
            )
    elif body.grantee_type == "group":
        grp = await db.get(Group, body.grantee_id)
        if not grp or grp.workspace_id != perm.workspace_id:
            raise HTTPException(
                status_code=400,
                detail="Grantee group does not belong to the resource's workspace",
            )

    actor_id = uuid.UUID(admin["sub"])
    try:
        await permission_service.share_resource(
            db,
            permission_id,
            body.grantee_type,
            body.grantee_id,
            body.permission,
            actor_id,
        )
    except Exception as e:
        logger.error("app.error.unhandled", category="app", error=str(e), exc_info=True)
        raise HTTPException(status_code=400, detail="Failed to share permission")

    await activity_service.log_activity(
        db,
        action="permission_shared",
        target_type="resource_permission",
        target_id=permission_id,
        actor_id=actor_id,
        detail={"grantee_type": body.grantee_type, "grantee_id": str(body.grantee_id)},
    )
    await db.commit()
    return {"status": "ok"}


@router.delete("/permissions/{permission_id}/share")
async def revoke_permission_share(
    permission_id: uuid.UUID,
    grantee_type: str = Query(..., pattern=r"^(user|group)$"),
    grantee_id: uuid.UUID = Query(...),
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        await permission_service.revoke_share(
            db, permission_id, grantee_type, grantee_id
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    await activity_service.log_activity(
        db,
        action="permission_revoked",
        target_type="resource_permission",
        target_id=permission_id,
        actor_id=uuid.UUID(admin["sub"]),
    )
    await db.commit()
    return {"status": "ok"}


@router.delete("/permissions/service/{service_name}", status_code=200)
@limiter.limit(settings.rate_limit_sensitive, key_func=user_or_ip_key)
async def purge_service_permissions(
    request: Request,
    service_name: str,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete ALL resource permissions for a service. Destructive."""
    count = await permission_service.purge_service_permissions(db, service_name)
    await activity_service.log_activity(
        db,
        action="permissions_purged",
        target_type="service",
        target_id=uuid.UUID(int=0),
        actor_id=uuid.UUID(admin["sub"]),
        detail={"service_name": service_name, "deleted_count": count},
    )
    await db.commit()
    logger.info(
        "admin.permissions.purged",
        category="audit",
        service_name=service_name,
        deleted_count=count,
        actor=admin["sub"],
    )
    return {"status": "ok", "deleted_count": count}


# ── Roles (RBAC) ─────────────────────────────────────────────────────


@router.get("/service-actions", response_model=list[ServiceActionResponse])
async def list_service_actions(
    service_name: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    return await role_service.list_service_actions(db, service_name=service_name)


@router.delete("/service-actions/{action_id}", status_code=204)
async def delete_service_action(
    action_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    deleted = await role_service.delete_service_action(db, action_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Service action not found")
    await activity_service.log_activity(
        db,
        action="service_action_deleted",
        target_type="service_action",
        target_id=action_id,
        actor_id=uuid.UUID(admin["sub"]),
    )
    await db.commit()


@router.get("/workspaces/{workspace_id}/roles", response_model=list[RoleResponse])
async def list_workspace_roles(
    workspace_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    return await role_service.list_workspace_roles(db, workspace_id)


@router.post(
    "/workspaces/{workspace_id}/roles", response_model=RoleResponse, status_code=201
)
async def create_role(
    workspace_id: uuid.UUID,
    body: RoleCreateRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    actor_id = uuid.UUID(admin["sub"])
    role = await role_service.create_role(
        db,
        workspace_id,
        body.name,
        body.description,
        created_by=actor_id,
    )
    await activity_service.log_activity(
        db,
        action="role_created",
        target_type="role",
        target_id=role.id,
        actor_id=actor_id,
        workspace_id=workspace_id,
        detail={"name": role.name},
    )
    await db.commit()
    # Return with counts
    roles = await role_service.list_workspace_roles(db, workspace_id)
    return next(r for r in roles if r["id"] == role.id)


@router.patch("/roles/{role_id}", response_model=RoleResponse)
async def update_role(
    role_id: uuid.UUID,
    body: RoleUpdateRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        role = await role_service.update_role(
            db, role_id, name=body.name, description=body.description
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    await activity_service.log_activity(
        db,
        action="role_updated",
        target_type="role",
        target_id=role_id,
        actor_id=uuid.UUID(admin["sub"]),
    )
    await db.commit()
    roles = await role_service.list_workspace_roles(db, role.workspace_id)
    return next(r for r in roles if r["id"] == role_id)


@router.delete("/roles/{role_id}", status_code=204)
async def delete_role(
    role_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        await role_service.delete_role(db, role_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    await activity_service.log_activity(
        db,
        action="role_deleted",
        target_type="role",
        target_id=role_id,
        actor_id=uuid.UUID(admin["sub"]),
    )
    await db.commit()


@router.get("/roles/{role_id}/actions", response_model=list[ServiceActionResponse])
async def list_role_actions(
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    return await role_service.list_role_actions(db, role_id)


@router.post("/roles/{role_id}/actions", status_code=201)
async def add_role_actions(
    role_id: uuid.UUID,
    body: AddRoleActionsRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        await role_service.add_role_actions(db, role_id, body.service_action_ids)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    await activity_service.log_activity(
        db,
        action="role_action_added",
        target_type="role",
        target_id=role_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"action_ids": [str(i) for i in body.service_action_ids]},
    )
    await db.commit()
    return {"status": "ok"}


@router.delete("/roles/{role_id}/actions/{service_action_id}", status_code=204)
async def remove_role_action(
    role_id: uuid.UUID,
    service_action_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        await role_service.remove_role_action(db, role_id, service_action_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    await activity_service.log_activity(
        db,
        action="role_action_removed",
        target_type="role",
        target_id=role_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"service_action_id": str(service_action_id)},
    )
    await db.commit()


@router.get("/roles/{role_id}/members", response_model=list[RoleMemberResponse])
async def list_role_members(
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    return await role_service.list_role_members(db, role_id)


@router.post("/roles/{role_id}/members/{user_id}", status_code=201)
async def assign_role_member(
    role_id: uuid.UUID,
    user_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        # Confirm the role exists for a clean 404. Admin tokens are global (the
        # User model has a single is_admin flag, no per-workspace admin), so
        # there is no separate workspace context to scope this against — passing
        # role.workspace_id here would be a tautology, not an isolation check.
        from src.models.role import Role

        role = await db.get(Role, role_id)
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        await role_service.assign_user_role(
            db,
            user_id,
            role_id,
            assigned_by=uuid.UUID(admin["sub"]),
        )
    except HTTPException:
        raise
    except ValueError as e:
        # Role existence is already 404'd above, so a ValueError here is the
        # target-not-a-workspace-member check — a bad request, not a 404.
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("app.error.unhandled", category="app", error=str(e), exc_info=True)
        raise HTTPException(status_code=400, detail="Failed to assign role")

    await activity_service.log_activity(
        db,
        action="role_member_added",
        target_type="user",
        target_id=user_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"role_id": str(role_id)},
    )
    await db.commit()
    return {"status": "ok"}


@router.delete("/roles/{role_id}/members/{user_id}", status_code=204)
async def remove_role_member(
    role_id: uuid.UUID,
    user_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        await role_service.remove_user_role(db, user_id, role_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    await activity_service.log_activity(
        db,
        action="role_member_removed",
        target_type="user",
        target_id=user_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"role_id": str(role_id)},
    )
    await db.commit()


@router.get("/roles/{role_id}/groups", response_model=list[RoleGroupResponse])
async def list_role_groups(
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    return await role_service.list_role_groups(db, role_id)


def _group_role_error(e: ValueError) -> HTTPException:
    detail = str(e)
    if "not found" in detail.lower():
        return HTTPException(status_code=404, detail=detail)
    if "already assigned" in detail.lower():
        return HTTPException(status_code=409, detail=detail)
    return HTTPException(status_code=400, detail=detail)


@router.post("/roles/{role_id}/groups/{group_id}", status_code=201)
async def assign_role_group(
    role_id: uuid.UUID,
    group_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        await role_service.assign_group_role(
            db, group_id, role_id, assigned_by=uuid.UUID(admin["sub"])
        )
    except ValueError as e:
        raise _group_role_error(e)

    await activity_service.log_activity(
        db,
        action="role_group_added",
        target_type="group",
        target_id=group_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"role_id": str(role_id)},
    )
    await db.commit()
    return {"status": "ok"}


@router.delete("/roles/{role_id}/groups/{group_id}", status_code=204)
async def remove_role_group(
    role_id: uuid.UUID,
    group_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        await role_service.remove_group_role(db, group_id, role_id)
    except ValueError as e:
        raise _group_role_error(e)

    await activity_service.log_activity(
        db,
        action="role_group_removed",
        target_type="group",
        target_id=group_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"role_id": str(role_id)},
    )
    await db.commit()


# ── Client Apps ───────────────────────────────────────────────────────


@router.get("/client-apps", response_model=list[ClientAppResponse])
async def list_client_apps(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ClientApp).order_by(ClientApp.created_at.desc()))
    return result.scalars().all()


@router.post("/client-apps", response_model=ClientAppResponse, status_code=201)
async def create_client_app(
    body: ClientAppCreateRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    app = ClientApp(
        id=uuid.uuid4(),
        name=body.name,
        redirect_uris=body.redirect_uris,
        created_by=uuid.UUID(admin["sub"]),
    )
    db.add(app)
    await activity_service.log_activity(
        db,
        action="client_app_created",
        target_type="client_app",
        target_id=app.id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"name": app.name},
    )
    await db.commit()
    await db.refresh(app)
    await refresh_origins(db)
    return app


@router.get("/client-apps/{app_id}", response_model=ClientAppResponse)
async def get_client_app(
    app_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    app = await db.get(ClientApp, app_id)
    if not app:
        raise HTTPException(status_code=404, detail="Client app not found")
    return app


@router.patch("/client-apps/{app_id}", response_model=ClientAppResponse)
async def update_client_app(
    app_id: uuid.UUID,
    body: ClientAppUpdateRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    app = await db.get(ClientApp, app_id)
    if not app:
        raise HTTPException(status_code=404, detail="Client app not found")
    if body.name is not None:
        app.name = body.name
    if body.redirect_uris is not None:
        app.redirect_uris = body.redirect_uris
    if body.is_active is not None:
        app.is_active = body.is_active
    tokens_revoked = 0
    if body.is_active is False and body.revoke_sessions:
        tokens_revoked = await token_service.revoke_app_tokens(str(app_id))
    await activity_service.log_activity(
        db,
        action="client_app_updated",
        target_type="client_app",
        target_id=app_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"name": app.name, "tokens_revoked": tokens_revoked},
    )
    await db.commit()
    await db.refresh(app)
    await refresh_origins(db)
    return app


@router.delete("/client-apps/{app_id}", status_code=204)
async def delete_client_app(
    app_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    app = await db.get(ClientApp, app_id)
    if not app:
        raise HTTPException(status_code=404, detail="Client app not found")
    await activity_service.log_activity(
        db,
        action="client_app_deleted",
        target_type="client_app",
        target_id=app_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"name": app.name},
    )
    await db.delete(app)
    await db.commit()
    await refresh_origins(db)


# ── Service Apps ──────────────────────────────────────────────────────


@router.get("/service-apps", response_model=list[ServiceAppResponse])
async def list_service_apps(db: AsyncSession = Depends(get_db)):
    return await service_app_service.list_service_apps(db)


@router.post("/service-apps", response_model=ServiceAppCreateResponse, status_code=201)
@limiter.limit(settings.rate_limit_sensitive, key_func=user_or_ip_key)
async def create_service_app(
    request: Request,
    body: ServiceAppCreateRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    actor_id = uuid.UUID(admin["sub"])
    try:
        app, plaintext_key = await service_app_service.create_service_app(
            db,
            name=body.name,
            service_name=body.service_name,
            created_by=actor_id,
            allowed_origins=body.allowed_origins,
            allowed_idp_audiences=body.allowed_idp_audiences,
        )
    except ValueError as e:
        # Actionable validation failure (e.g. name collides with a realm slug) —
        # surface it like create_realm does, not as a generic 400.
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("app.error.unhandled", category="app", error=str(e), exc_info=True)
        raise HTTPException(status_code=400, detail="Failed to create service app")

    await activity_service.log_activity(
        db,
        action="service_app_created",
        target_type="service_app",
        target_id=app.id,
        actor_id=actor_id,
        detail={"name": app.name, "service_name": app.service_name},
    )
    await db.commit()
    await db.refresh(app)
    await service_app_service.invalidate_cache()
    await refresh_origins(db)
    return ServiceAppCreateResponse(
        id=app.id,
        name=app.name,
        service_name=app.service_name,
        key_prefix=app.key_prefix,
        is_active=app.is_active,
        allowed_origins=app.allowed_origins,
        allowed_idp_audiences=app.allowed_idp_audiences,
        last_used_at=app.last_used_at,
        created_by=app.created_by,
        created_at=app.created_at,
        updated_at=app.updated_at,
        api_key=plaintext_key,
    )


@router.get("/service-apps/{app_id}", response_model=ServiceAppResponse)
async def get_service_app(
    app_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    app = await service_app_service.get_service_app(db, app_id)
    if not app:
        raise HTTPException(status_code=404, detail="Service app not found")
    return app


@router.patch("/service-apps/{app_id}", response_model=ServiceAppResponse)
async def update_service_app(
    app_id: uuid.UUID,
    body: ServiceAppUpdateRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        app = await service_app_service.update_service_app(
            db,
            app_id,
            name=body.name,
            is_active=body.is_active,
            allowed_origins=body.allowed_origins,
            allowed_idp_audiences=body.allowed_idp_audiences,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    await activity_service.log_activity(
        db,
        action="service_app_updated",
        target_type="service_app",
        target_id=app_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"name": app.name, "is_active": app.is_active},
    )
    await db.commit()
    await db.refresh(app)
    await service_app_service.invalidate_cache()
    await refresh_origins(db)
    return app


@router.post(
    "/service-apps/{app_id}/rotate-key", response_model=ServiceAppCreateResponse
)
@limiter.limit(settings.rate_limit_sensitive, key_func=user_or_ip_key)
async def rotate_service_app_key(
    request: Request,
    app_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        app, plaintext_key = await service_app_service.rotate_key(db, app_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    await activity_service.log_activity(
        db,
        action="service_app_key_rotated",
        target_type="service_app",
        target_id=app_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"name": app.name},
    )
    await db.commit()
    await db.refresh(app)
    await service_app_service.invalidate_cache()
    return ServiceAppCreateResponse(
        id=app.id,
        name=app.name,
        service_name=app.service_name,
        key_prefix=app.key_prefix,
        is_active=app.is_active,
        allowed_origins=app.allowed_origins,
        allowed_idp_audiences=app.allowed_idp_audiences,
        last_used_at=app.last_used_at,
        created_by=app.created_by,
        created_at=app.created_at,
        updated_at=app.updated_at,
        api_key=plaintext_key,
    )


@router.delete("/service-apps/{app_id}", status_code=204)
async def delete_service_app(
    app_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    app = await service_app_service.get_service_app(db, app_id)
    if not app:
        raise HTTPException(status_code=404, detail="Service app not found")
    await activity_service.log_activity(
        db,
        action="service_app_deleted",
        target_type="service_app",
        target_id=app_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"name": app.name, "service_name": app.service_name},
    )
    perm_count = await permission_service.purge_service_permissions(
        db, app.service_name
    )
    if perm_count:
        logger.info(
            "admin.service.permissions_purged_on_delete",
            category="audit",
            service_name=app.service_name,
            deleted_count=perm_count,
        )
    await service_app_service.delete_service_app(db, app_id)
    await db.commit()
    await service_app_service.invalidate_cache()
    await refresh_origins(db)


# ── CSV Import ────────────────────────────────────────────────────────

_MAX_CSV_SIZE = 5 * 1024 * 1024  # 5 MB


async def _read_csv(file: UploadFile) -> str:
    content = await file.read()
    if len(content) > _MAX_CSV_SIZE:
        raise HTTPException(status_code=413, detail="CSV file too large (max 5 MB)")
    return content.decode("utf-8")


@router.post("/import/csv/preview", response_model=CsvImportPreview)
@limiter.limit(settings.rate_limit_sensitive, key_func=user_or_ip_key)
async def csv_preview(request: Request, file: UploadFile = File(...)):
    content = await _read_csv(file)
    return admin_service.parse_csv(content)


@router.post("/import/csv/execute", response_model=CsvImportResult)
@limiter.limit(settings.rate_limit_sensitive, key_func=user_or_ip_key)
async def csv_execute(
    request: Request,
    file: UploadFile = File(...),
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    content = await _read_csv(file)
    preview = admin_service.parse_csv(content)
    return await admin_service.execute_import(
        db,
        preview["rows"],
        actor_id=uuid.UUID(admin["sub"]),
    )


# ── Data Export ──────────────────────────────────────────────────────


@router.get("/export/users")
async def export_users(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(User, func.count(WorkspaceMembership.id).label("workspace_count"))
        .outerjoin(WorkspaceMembership, User.id == WorkspaceMembership.user_id)
        .group_by(User.id)
        .order_by(User.created_at.desc())
    )
    result = await db.execute(stmt)

    def generate():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "id",
                "email",
                "name",
                "is_active",
                "is_admin",
                "created_at",
                "workspace_count",
            ]
        )
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)
        for user, ws_count in result.all():
            writer.writerow(
                [
                    str(user.id),
                    user.email,
                    user.name,
                    user.is_active,
                    user.is_admin,
                    user.created_at.isoformat(),
                    ws_count,
                ]
            )
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

    await activity_service.log_activity(
        db,
        action="export_users",
        target_type="export",
        target_id=uuid.UUID(int=0),
        actor_id=uuid.UUID(admin["sub"]),
    )
    await db.commit()

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=users.csv"},
    )


@router.get("/export/workspaces")
async def export_workspaces(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Workspace, func.count(WorkspaceMembership.id).label("member_count"))
        .outerjoin(
            WorkspaceMembership, Workspace.id == WorkspaceMembership.workspace_id
        )
        .group_by(Workspace.id)
        .order_by(Workspace.created_at.desc())
    )
    result = await db.execute(stmt)

    def generate():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "id",
                "slug",
                "name",
                "description",
                "created_by",
                "created_at",
                "member_count",
            ]
        )
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)
        for ws, m_count in result.all():
            writer.writerow(
                [
                    str(ws.id),
                    ws.slug,
                    ws.name,
                    ws.description or "",
                    str(ws.created_by),
                    ws.created_at.isoformat(),
                    m_count,
                ]
            )
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

    await activity_service.log_activity(
        db,
        action="export_workspaces",
        target_type="export",
        target_id=uuid.UUID(int=0),
        actor_id=uuid.UUID(admin["sub"]),
    )
    await db.commit()

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=workspaces.csv"},
    )


# ── Realms ────────────────────────────────────────────────────────────


@router.get("/realms", response_model=list[RealmResponse])
async def list_realms(db: AsyncSession = Depends(get_db)):
    return await realm_service.list_realms(db)


@router.post("/realms", response_model=RealmResponse, status_code=201)
async def create_realm(
    body: RealmCreateRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    actor_id = uuid.UUID(admin["sub"])
    try:
        realm = await realm_service.create_realm(
            db,
            name=body.name,
            slug=body.slug,
            m2m_ttl_s=body.m2m_ttl_s,
            created_by=actor_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await activity_service.log_activity(
        db,
        action="realm_created",
        target_type="realm",
        target_id=realm.id,
        actor_id=actor_id,
        detail={"name": realm.name, "slug": realm.slug},
    )
    await db.commit()
    return realm


@router.get("/realms/{realm_id}", response_model=RealmResponse)
async def get_realm_detail(realm_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    realm = await realm_service.get_realm(db, realm_id)
    if realm is None:
        raise HTTPException(status_code=404, detail="Realm not found")
    return realm


@router.patch("/realms/{realm_id}", response_model=RealmResponse)
async def update_realm(
    realm_id: uuid.UUID,
    body: RealmUpdateRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    realm = await realm_service.update_realm(
        db, realm_id, name=body.name, m2m_ttl_s=body.m2m_ttl_s, is_active=body.is_active
    )
    if realm is None:
        raise HTTPException(status_code=404, detail="Realm not found")
    await activity_service.log_activity(
        db,
        action="realm_updated",
        target_type="realm",
        target_id=realm_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"name": realm.name},
    )
    await db.commit()
    return realm


@router.delete("/realms/{realm_id}", status_code=204)
async def delete_realm(
    realm_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    deleted = await realm_service.delete_realm(db, realm_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Realm not found")
    await activity_service.log_activity(
        db,
        action="realm_deleted",
        target_type="realm",
        target_id=realm_id,
        actor_id=uuid.UUID(admin["sub"]),
    )
    await db.commit()
    await service_app_service.invalidate_cache()


async def _member_response(db, app) -> RealmMemberResponse:
    return RealmMemberResponse(
        id=app.id,
        name=app.name,
        service_name=app.service_name,
        has_grants=await realm_service.service_app_has_grants(db, app.service_name),
    )


@router.get("/realms/{realm_id}/members", response_model=list[RealmMemberResponse])
async def list_realm_members(realm_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    members = await realm_service.list_members(db, realm_id)
    return [await _member_response(db, app) for app in members]


@router.post(
    "/realms/{realm_id}/members/{service_app_id}",
    response_model=RealmMemberResponse,
    status_code=201,
)
async def add_realm_member(
    realm_id: uuid.UUID,
    service_app_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    realm = await realm_service.get_realm(db, realm_id)
    if realm is None:
        raise HTTPException(status_code=404, detail="Realm not found")
    app = await service_app_service.get_service_app(db, service_app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Service app not found")
    # ponytail: one-realm-max — refuse to steal an app from another realm
    if app.realm_id is not None and app.realm_id != realm_id:
        raise HTTPException(
            status_code=409,
            detail="Service app already belongs to another realm; remove it first",
        )
    await realm_service.add_member(db, realm_id, service_app_id)
    await activity_service.log_activity(
        db,
        action="realm_member_added",
        target_type="realm",
        target_id=realm_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={
            "service_app_id": str(service_app_id),
            "service_name": app.service_name,
        },
    )
    await db.commit()
    await service_app_service.invalidate_cache()
    return await _member_response(db, app)


@router.delete("/realms/{realm_id}/members/{service_app_id}", status_code=204)
async def remove_realm_member(
    realm_id: uuid.UUID,
    service_app_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    app = await service_app_service.get_service_app(db, service_app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Service app not found")
    if app.realm_id != realm_id:
        raise HTTPException(
            status_code=404, detail="Service app is not a member of this realm"
        )
    await realm_service.remove_member(db, service_app_id)
    await activity_service.log_activity(
        db,
        action="realm_member_removed",
        target_type="realm",
        target_id=realm_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={
            "service_app_id": str(service_app_id),
            "service_name": app.service_name,
        },
    )
    await db.commit()
    await service_app_service.invalidate_cache()
