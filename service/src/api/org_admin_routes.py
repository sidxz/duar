"""Admin endpoints for organizations, domains, and workspace allowed-orgs."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import require_admin
from src.config import settings
from src.database import get_db
from src.middleware.rate_limit import limiter, user_or_ip_key
from src.schemas.admin import (
    AdminOrgCreateRequest,
    AdminOrgDetailResponse,
    AdminOrgDomainCreateRequest,
    AdminOrgDomainResponse,
    AdminOrgResponse,
    AdminOrgUpdateRequest,
    AdminUserResponse,
    AdminWorkspaceAllowedOrgsRequest,
    AdminWorkspaceAllowedOrgsResponse,
    PaginatedResponse,
)
from src.services import activity_service, org_admin_service
from src.services.org_admin_service import OrgConflict, OrgNotFound, OrgProtected

router = APIRouter(
    prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)]
)


async def _commit_or_conflict(
    db: AsyncSession, *, conflict_detail: str, fk_detail: str | None = None
) -> None:
    """Commit, mapping a constraint race to a precise HTTP error.

    A unique-violation (a concurrent insert won the race) -> 409 ``conflict_detail``.
    A foreign-key violation (a referenced row deleted concurrently, or an ON DELETE
    RESTRICT backstop firing) -> 400 ``fk_detail`` when provided — so a permanently
    failing error is never reported as a transient 'please retry'.
    """
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        pgcode = getattr(getattr(e, "orig", None), "sqlstate", None)
        if fk_detail is not None and pgcode == "23503":  # foreign_key_violation
            raise HTTPException(status_code=400, detail=fk_detail)
        raise HTTPException(status_code=409, detail=conflict_detail)


def _org_response(org, domain_count: int, user_count: int) -> AdminOrgResponse:
    return AdminOrgResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        is_public=org.is_public,
        enabled=org.enabled,
        domain_count=domain_count,
        user_count=user_count,
    )


def _detail_response(detail: dict) -> AdminOrgDetailResponse:
    org = detail["org"]
    return AdminOrgDetailResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        is_public=org.is_public,
        enabled=org.enabled,
        user_count=detail["user_count"],
        domains=[AdminOrgDomainResponse.model_validate(d) for d in detail["domains"]],
    )


@router.get("/organizations", response_model=list[AdminOrgResponse])
async def list_organizations(db: AsyncSession = Depends(get_db)):
    rows = await org_admin_service.list_organizations(db)
    return [_org_response(r["org"], r["domain_count"], r["user_count"]) for r in rows]


@router.post("/organizations", response_model=AdminOrgResponse, status_code=201)
@limiter.limit(settings.rate_limit_sensitive, key_func=user_or_ip_key)
async def create_organization(
    request: Request,
    body: AdminOrgCreateRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    actor_id = uuid.UUID(admin["sub"])
    try:
        org = await org_admin_service.create_organization(
            db, name=body.name, slug=body.slug, created_by=actor_id
        )
    except OrgConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    await activity_service.log_activity(
        db,
        action="org_create",
        target_type="organization",
        target_id=org.id,
        actor_id=actor_id,
        detail={"name": org.name, "slug": org.slug},
    )
    await _commit_or_conflict(
        db, conflict_detail=f"Organization slug {body.slug!r} is already in use"
    )
    return _org_response(org, 0, 0)


@router.get("/organizations/{org_id}", response_model=AdminOrgDetailResponse)
async def get_organization(org_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    detail = await org_admin_service.get_organization_detail(db, org_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return _detail_response(detail)


@router.patch("/organizations/{org_id}", response_model=AdminOrgDetailResponse)
async def update_organization(
    org_id: uuid.UUID,
    body: AdminOrgUpdateRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        org, enabled_changed = await org_admin_service.update_organization(
            db, org_id, name=body.name, enabled=body.enabled
        )
    except OrgNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    # Distinct audit action only when the public org's sign-in switch ACTUALLY
    # changes value; a rename or an unchanged-`enabled` echo is a normal org_update
    # and must not pollute the audit trail with a security-relevant toggle event.
    action = (
        "org_public_toggle" if (org.is_public and enabled_changed) else "org_update"
    )
    detail = await org_admin_service.get_organization_detail(db, org_id)
    await activity_service.log_activity(
        db,
        action=action,
        target_type="organization",
        target_id=org.id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"name": org.name, "enabled": org.enabled},
    )
    await db.commit()
    return _detail_response(detail)


@router.delete("/organizations/{org_id}", status_code=204)
async def delete_organization(
    org_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        await org_admin_service.delete_organization(db, org_id)
    except OrgNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except OrgProtected as e:
        raise HTTPException(status_code=400, detail=str(e))
    await activity_service.log_activity(
        db,
        action="org_delete",
        target_type="organization",
        target_id=org_id,
        actor_id=uuid.UUID(admin["sub"]),
    )
    # Race backstop for the ON DELETE RESTRICT FK: a workspace allow-list
    # referencing this org may have been added between the pre-check and commit.
    await _commit_or_conflict(
        db,
        conflict_detail="Could not delete the organization.",
        fk_detail="This organization is referenced by a workspace's allowed-orgs "
        "list. Remove it from those workspaces before deleting it.",
    )


@router.post(
    "/organizations/{org_id}/domains",
    response_model=AdminOrgDomainResponse,
    status_code=201,
)
@limiter.limit(settings.rate_limit_admin_write, key_func=user_or_ip_key)
async def add_domain(
    request: Request,
    org_id: uuid.UUID,
    body: AdminOrgDomainCreateRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        row = await org_admin_service.add_domain(
            db,
            org_id,
            body.domain,
            include_subdomains=body.include_subdomains,
        )
    except OrgNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except OrgProtected as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OrgConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    domain_payload = AdminOrgDomainResponse.model_validate(row)
    await activity_service.log_activity(
        db,
        action="org_domain_add",
        target_type="organization",
        target_id=org_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"domain": row.domain, "include_subdomains": row.include_subdomains},
    )
    await _commit_or_conflict(
        db,
        conflict_detail=f"Domain {row.domain!r} is already claimed by an organization",
    )
    return domain_payload


@router.delete("/organizations/{org_id}/domains/{domain_id}", status_code=204)
async def remove_domain(
    org_id: uuid.UUID,
    domain_id: uuid.UUID,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        await org_admin_service.remove_domain(db, org_id, domain_id)
    except OrgNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    await activity_service.log_activity(
        db,
        action="org_domain_remove",
        target_type="organization",
        target_id=org_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"domain_id": str(domain_id)},
    )
    await db.commit()


@router.get("/organizations/{org_id}/users", response_model=PaginatedResponse)
async def list_org_users(
    org_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    try:
        users, total = await org_admin_service.list_org_users(
            db, org_id, page=page, page_size=page_size
        )
    except OrgNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    items = [
        AdminUserResponse(
            id=u.id,
            email=u.email,
            name=u.name,
            avatar_url=u.avatar_url,
            is_active=u.is_active,
            is_admin=u.is_admin,
            created_at=u.created_at,
            workspace_count=workspace_count,
        )
        for u, workspace_count in users
    ]
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/workspaces/{workspace_id}/allowed-organizations",
    response_model=AdminWorkspaceAllowedOrgsResponse,
)
async def get_allowed_orgs(workspace_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    try:
        orgs = await org_admin_service.get_workspace_allowed_orgs(db, workspace_id)
    except OrgNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    return AdminWorkspaceAllowedOrgsResponse(organization_ids=[o.id for o in orgs])


@router.put(
    "/workspaces/{workspace_id}/allowed-organizations",
    response_model=AdminWorkspaceAllowedOrgsResponse,
)
async def set_allowed_orgs(
    workspace_id: uuid.UUID,
    body: AdminWorkspaceAllowedOrgsRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        saved_ids = await org_admin_service.set_workspace_allowed_orgs(
            db, workspace_id, body.organization_ids
        )
    except OrgNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await activity_service.log_activity(
        db,
        action="workspace_allowed_orgs_set",
        target_type="workspace",
        target_id=workspace_id,
        actor_id=uuid.UUID(admin["sub"]),
        detail={"organization_ids": [str(i) for i in saved_ids]},
    )
    await _commit_or_conflict(
        db,
        conflict_detail="Allowed organizations changed concurrently. Re-open the "
        "tab and save again.",
        fk_detail="One of the selected organizations was just deleted. Refresh the "
        "list and try again.",
    )
    return AdminWorkspaceAllowedOrgsResponse(organization_ids=saved_ids)
