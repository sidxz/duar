import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.logging_events import log_audit, log_security
from src.middleware.rate_limit import limiter, service_or_ip_key

from src.api.dependencies import (
    CurrentUser,
    ServiceKeyContext,
    get_user_for_service_call,
    require_service_key,
    verify_service_scope,
)
from src.database import get_db
from src.schemas.permission import (
    AccessibleResourcesRequest,
    AccessibleResourcesResponse,
    EnrichedResourcePermissionResponse,
    PermissionCheckRequest,
    PermissionCheckResponse,
    PermissionCheckResult,
    RegisterResourceRequest,
    ResourcePermissionResponse,
    ShareRequest,
    UpdateVisibilityRequest,
)
from src.services import permission_service

logger = structlog.get_logger()

router = APIRouter(prefix="/permissions", tags=["permissions"])


# --- Dual auth: service key + user JWT ---


@router.post("/check", response_model=PermissionCheckResponse)
async def check_permissions(
    body: PermissionCheckRequest,
    svc: ServiceKeyContext = Depends(require_service_key),
    user: CurrentUser = Depends(get_user_for_service_call),
    db: AsyncSession = Depends(get_db),
):
    for item in body.checks:
        verify_service_scope(svc, item.service_name)
    results = []
    for item in body.checks:
        allowed = await permission_service.check_permission(
            db,
            user_id=user.user_id,
            workspace_id=user.workspace_id,
            workspace_role=user.workspace_role,
            group_ids=user.groups,
            service_name=item.service_name,
            resource_type=item.resource_type,
            resource_id=item.resource_id,
            action=item.action,
        )
        if not allowed:
            # Stream-only: entity-ACL denials include legitimate UI-affordance
            # checks (hot path), so they get a security event but no activity
            # row — unlike the rarer RBAC action_denied.
            log_security(
                "permission.check.denied",
                outcome="denied",
                caller_service=svc.service_name,
                actor=str(user.user_id),
                workspace_id=str(user.workspace_id),
                service_name=item.service_name,
                resource_type=item.resource_type,
                resource_id=str(item.resource_id),
                action=item.action,
            )
        results.append(
            PermissionCheckResult(
                service_name=item.service_name,
                resource_type=item.resource_type,
                resource_id=item.resource_id,
                action=item.action,
                allowed=allowed,
            )
        )
    return PermissionCheckResponse(results=results)


@router.post("/accessible", response_model=AccessibleResourcesResponse)
async def accessible_resources(
    body: AccessibleResourcesRequest,
    svc: ServiceKeyContext = Depends(require_service_key),
    user: CurrentUser = Depends(get_user_for_service_call),
    db: AsyncSession = Depends(get_db),
):
    verify_service_scope(svc, body.service_name)
    if body.workspace_id != user.workspace_id:
        raise HTTPException(
            status_code=403, detail="Cross-workspace lookup not allowed"
        )

    (
        resource_ids,
        has_full_access,
    ) = await permission_service.lookup_accessible_resources(
        db,
        user_id=user.user_id,
        workspace_id=user.workspace_id,
        workspace_role=user.workspace_role,
        group_ids=user.groups,
        service_name=body.service_name,
        resource_type=body.resource_type,
        action=body.action,
        limit=body.limit,
    )
    return AccessibleResourcesResponse(
        resource_ids=resource_ids, has_full_access=has_full_access
    )


@router.post("/{permission_id}/share", status_code=201)
async def share_resource(
    permission_id: uuid.UUID,
    body: ShareRequest,
    svc: ServiceKeyContext = Depends(require_service_key),
    user: CurrentUser = Depends(get_user_for_service_call),
    db: AsyncSession = Depends(get_db),
):
    # Verify service scope via the permission's service_name
    perm = await permission_service.get_permission_by_id(db, permission_id)
    if not perm:
        raise HTTPException(status_code=404, detail="Permission not found")
    verify_service_scope(svc, perm.service_name)

    # Ownership check: workspace isolation + owner or admin
    if perm.workspace_id != user.workspace_id:
        raise HTTPException(
            status_code=403, detail="Cross-workspace sharing not allowed"
        )
    if perm.owner_id != user.user_id and user.workspace_role not in ("owner", "admin"):
        raise HTTPException(
            status_code=403,
            detail="Only the resource owner or workspace admin can share",
        )

    try:
        await permission_service.share_resource(
            db,
            permission_id=permission_id,
            grantee_type=body.grantee_type,
            grantee_id=body.grantee_id,
            permission=body.permission,
            granted_by=user.user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # An ACL grant — the mirror of share_revoked below; without this, shares
    # are invisible while unshares are recorded.
    log_audit(
        "permission.resource.shared",
        caller_service=svc.service_name,
        permission_id=str(permission_id),
        grantee_type=body.grantee_type,
        grantee_id=str(body.grantee_id),
        permission=body.permission,
        granted_by=str(user.user_id),
    )
    return {"status": "ok"}


# --- Service-only auth: service key ---


@router.post("/register", response_model=ResourcePermissionResponse, status_code=201)
async def register_resource(
    body: RegisterResourceRequest,
    svc: ServiceKeyContext = Depends(require_service_key),
    db: AsyncSession = Depends(get_db),
):
    verify_service_scope(svc, body.service_name)
    try:
        perm = await permission_service.register_resource(
            db,
            service_name=body.service_name,
            resource_type=body.resource_type,
            resource_id=body.resource_id,
            workspace_id=body.workspace_id,
            owner_id=body.owner_id,
            visibility=body.visibility,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    log_audit(
        "permission.resource.registered",
        caller_service=svc.service_name,
        resource_type=body.resource_type,
        resource_id=str(body.resource_id),
        workspace_id=str(body.workspace_id),
        owner_id=str(body.owner_id),
    )
    return perm


@router.patch("/{permission_id}/visibility", response_model=ResourcePermissionResponse)
async def update_visibility(
    permission_id: uuid.UUID,
    body: UpdateVisibilityRequest,
    svc: ServiceKeyContext = Depends(require_service_key),
    db: AsyncSession = Depends(get_db),
):
    perm = await permission_service.get_permission_by_id(db, permission_id)
    if not perm:
        raise HTTPException(status_code=404, detail="Permission not found")
    verify_service_scope(svc, perm.service_name)
    result = await permission_service.update_visibility(
        db, permission_id, body.visibility
    )
    log_audit(
        "permission.resource.visibility_updated",
        caller_service=svc.service_name,
        permission_id=str(permission_id),
        visibility=body.visibility,
    )
    return result


@router.delete("/{permission_id}/share")
async def revoke_share(
    permission_id: uuid.UUID,
    body: ShareRequest,
    svc: ServiceKeyContext = Depends(require_service_key),
    db: AsyncSession = Depends(get_db),
):
    perm = await permission_service.get_permission_by_id(db, permission_id)
    if not perm:
        raise HTTPException(status_code=404, detail="Permission not found")
    verify_service_scope(svc, perm.service_name)
    try:
        await permission_service.revoke_share(
            db,
            permission_id=permission_id,
            grantee_type=body.grantee_type,
            grantee_id=body.grantee_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    log_audit(
        "permission.resource.share_revoked",
        caller_service=svc.service_name,
        permission_id=str(permission_id),
        grantee_type=body.grantee_type,
        grantee_id=str(body.grantee_id),
    )
    return {"status": "ok"}


@router.delete(
    "/resource/{service_name}/{resource_type}/{resource_id}", status_code=204
)
async def deregister_resource(
    service_name: str,
    resource_type: str,
    resource_id: uuid.UUID,
    svc: ServiceKeyContext = Depends(require_service_key),
    db: AsyncSession = Depends(get_db),
):
    """Delete a resource permission and all its shares."""
    verify_service_scope(svc, service_name)
    try:
        await permission_service.deregister_resource(
            db, service_name, resource_type, resource_id
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Resource not found")
    log_audit(
        "permission.resource.deregistered",
        caller_service=svc.service_name,
        resource_type=resource_type,
        resource_id=str(resource_id),
    )


@router.get(
    "/resource/{service_name}/{resource_type}/{resource_id}",
    response_model=ResourcePermissionResponse,
)
async def get_resource_acl(
    service_name: str,
    resource_type: str,
    resource_id: uuid.UUID,
    svc: ServiceKeyContext = Depends(require_service_key),
    db: AsyncSession = Depends(get_db),
):
    verify_service_scope(svc, service_name)
    perm = await permission_service.get_resource_permission(
        db, service_name, resource_type, resource_id
    )
    if not perm:
        raise HTTPException(status_code=404, detail="Resource not found")
    return perm


@router.get(
    "/resource/{service_name}/{resource_type}/{resource_id}/enriched",
    response_model=EnrichedResourcePermissionResponse,
)
# Service-key-authed (require_service_key binds caller_service, never actor), so
# key per calling service — user_or_ip_key would always fall back to IP here.
@limiter.limit(settings.rate_limit_read, key_func=service_or_ip_key)
async def get_enriched_resource_acl(
    request: Request,
    service_name: str,
    resource_type: str,
    resource_id: uuid.UUID,
    svc: ServiceKeyContext = Depends(require_service_key),
    db: AsyncSession = Depends(get_db),
):
    """Get resource ACL with user profiles resolved inline (names, emails)."""
    verify_service_scope(svc, service_name)
    result = await permission_service.get_enriched_resource_permission(
        db, service_name, resource_type, resource_id
    )
    if not result:
        raise HTTPException(status_code=404, detail="Resource not found")
    return result
