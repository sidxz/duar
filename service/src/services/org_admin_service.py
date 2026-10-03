"""Admin CRUD + guards for organizations, domains, and workspace allowed-orgs.

Kept separate from organization_service.py (the hot-path resolution/enforcement
module used on every sign-in). Raises typed exceptions the routes map to HTTP:
OrgNotFound -> 404, OrgConflict -> 409, OrgProtected -> 400.
"""

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.organization import (
    Organization,
    OrganizationDomain,
    WorkspaceAllowedOrganization,
)
from src.models.user import User
from src.models.workspace import Workspace, WorkspaceMembership
from src.services import organization_service


class OrgNotFound(Exception):
    """Organization / domain / workspace does not exist."""


class OrgConflict(Exception):
    """Slug or domain already in use."""


class OrgProtected(Exception):
    """Operation not allowed on the public organization."""


async def create_organization(
    db: AsyncSession, name: str, slug: str, created_by: uuid.UUID | None = None
) -> Organization:
    # DB unique constraint on slug is the concurrency backstop (the route maps the
    # resulting IntegrityError to 409); this pre-check just yields a cleaner error
    # in the common (non-racing) case.
    taken = (
        await db.execute(select(Organization.id).where(Organization.slug == slug))
    ).scalar_one_or_none()
    if taken is not None:
        raise OrgConflict(f"Organization slug {slug!r} is already in use")
    org = Organization(
        name=name, slug=slug, is_public=False, enabled=True, created_by=created_by
    )
    db.add(org)
    await db.flush()
    return org


async def update_organization(
    db: AsyncSession,
    org_id: uuid.UUID,
    name: str | None = None,
    enabled: bool | None = None,
) -> tuple[Organization, bool]:
    """Update an org's name/enabled. Returns ``(org, enabled_changed)`` so the route
    can audit a genuine public-sign-in toggle separately from a no-op echo/rename."""
    org = await db.get(Organization, org_id)
    if org is None:
        raise OrgNotFound("Organization not found")
    if name is not None:
        org.name = name
    enabled_changed = enabled is not None and enabled != org.enabled
    if enabled is not None:
        org.enabled = enabled
    await db.flush()
    return org, enabled_changed


async def delete_organization(db: AsyncSession, org_id: uuid.UUID) -> None:
    org = await db.get(Organization, org_id)
    if org is None:
        raise OrgNotFound("Organization not found")
    if org.is_public:
        raise OrgProtected("The public organization cannot be deleted")
    # Fail closed on the allow-list fail-open: removing an org from a workspace's
    # allow-list (e.g. via cascade) could empty it, and an empty allow-list means
    # 'open to all' — silently flipping a locked-down workspace open. The FK is
    # ON DELETE RESTRICT as the race-proof DB backstop; this pre-check refuses the
    # delete early with an actionable message whenever the org is referenced by ANY
    # workspace's allow-list, so the admin adjusts those workspaces first.
    referencing = await organization_service.workspaces_allowing_org(db, org_id)
    if referencing:
        raise OrgProtected(
            f"This organization is in the allowed-orgs list of {len(referencing)} "
            "workspace(s). Remove it from their access settings before deleting it, "
            "otherwise those workspaces could become open to members from any "
            "organization."
        )
    await db.delete(org)
    await db.flush()


async def list_organizations(db: AsyncSession) -> list[dict]:
    """List orgs with domain + user counts; public org sorted first."""
    dc = (
        select(
            OrganizationDomain.organization_id.label("oid"),
            func.count().label("c"),
        )
        .group_by(OrganizationDomain.organization_id)
        .subquery()
    )
    uc = (
        select(User.organization_id.label("oid"), func.count().label("c"))
        .group_by(User.organization_id)
        .subquery()
    )
    stmt = (
        select(
            Organization,
            func.coalesce(dc.c.c, 0),
            func.coalesce(uc.c.c, 0),
        )
        .outerjoin(dc, dc.c.oid == Organization.id)
        .outerjoin(uc, uc.c.oid == Organization.id)
        .order_by(Organization.is_public.desc(), Organization.name)
    )
    rows = (await db.execute(stmt)).all()
    return [{"org": org, "domain_count": d, "user_count": u} for org, d, u in rows]


async def get_organization_detail(db: AsyncSession, org_id: uuid.UUID) -> dict | None:
    org = await db.get(Organization, org_id)
    if org is None:
        return None
    domains = (
        (
            await db.execute(
                select(OrganizationDomain)
                .where(OrganizationDomain.organization_id == org_id)
                .order_by(OrganizationDomain.domain)
            )
        )
        .scalars()
        .all()
    )
    user_count = (
        await db.execute(select(func.count()).where(User.organization_id == org_id))
    ).scalar_one()
    return {"org": org, "domains": list(domains), "user_count": user_count}


async def add_domain(
    db: AsyncSession,
    org_id: uuid.UUID,
    domain: str,
    include_subdomains: bool,
) -> OrganizationDomain:
    org = await db.get(Organization, org_id)
    if org is None:
        raise OrgNotFound("Organization not found")
    if org.is_public:
        raise OrgProtected(
            "The public organization is the catch-all and cannot have domains"
        )
    normalized = organization_service.normalize_domain(domain)
    if normalized is None:
        raise ValueError(f"Invalid domain: {domain!r}")
    taken = (
        await db.execute(
            select(OrganizationDomain.id).where(OrganizationDomain.domain == normalized)
        )
    ).scalar_one_or_none()
    if taken is not None:
        raise OrgConflict(
            f"Domain {normalized!r} is already claimed by an organization"
        )
    row = OrganizationDomain(
        organization_id=org_id,
        domain=normalized,
        include_subdomains=include_subdomains,
    )
    db.add(row)
    await db.flush()
    return row


async def remove_domain(
    db: AsyncSession, org_id: uuid.UUID, domain_id: uuid.UUID
) -> None:
    row = await db.get(OrganizationDomain, domain_id)
    if row is None or row.organization_id != org_id:
        raise OrgNotFound("Domain not found")
    await db.delete(row)
    await db.flush()


async def get_workspace_allowed_orgs(
    db: AsyncSession, workspace_id: uuid.UUID
) -> list[Organization]:
    # Validate existence like the PUT counterpart, so 'workspace not found' is a
    # 404 and not indistinguishable from a real 'open to all orgs' empty list.
    workspace = await db.get(Workspace, workspace_id)
    if workspace is None:
        raise OrgNotFound("Workspace not found")
    stmt = (
        select(Organization)
        .join(
            WorkspaceAllowedOrganization,
            WorkspaceAllowedOrganization.organization_id == Organization.id,
        )
        .where(WorkspaceAllowedOrganization.workspace_id == workspace_id)
        .order_by(Organization.name)
    )
    return list((await db.execute(stmt)).scalars().all())


async def set_workspace_allowed_orgs(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    organization_ids: list[uuid.UUID],
) -> list[uuid.UUID]:
    """Replace the workspace's allowed-org set. Empty list = open to all.

    Returns the canonical (deduped) ids actually persisted, so the route echoes
    what was stored rather than the raw request body.
    """
    workspace = await db.get(Workspace, workspace_id)
    if workspace is None:
        raise OrgNotFound("Workspace not found")
    ids = list(dict.fromkeys(organization_ids))  # dedupe, keep order
    if ids:
        orgs = list(
            (await db.execute(select(Organization).where(Organization.id.in_(ids))))
            .scalars()
            .all()
        )
        found = {o.id for o in orgs}
        missing = [i for i in ids if i not in found]
        if missing:
            raise ValueError(f"Unknown organization ids: {[str(m) for m in missing]}")
        # An allow-list restricts membership to specific real, active tenants. The
        # public org (the orgless catch-all) and disabled orgs can never mint a
        # token, so allow-listing them would lock out every real member or make the
        # restriction meaningless. Reject up front rather than silently bricking the
        # workspace at token-issuance time.
        invalid = [o for o in orgs if o.is_public or not o.enabled]
        if invalid:
            raise ValueError(
                "Allowed organizations must be enabled, non-public organizations; "
                f"rejected: {sorted(o.slug for o in invalid)}"
            )
    await db.execute(
        delete(WorkspaceAllowedOrganization).where(
            WorkspaceAllowedOrganization.workspace_id == workspace_id
        )
    )
    for oid in ids:
        db.add(
            WorkspaceAllowedOrganization(workspace_id=workspace_id, organization_id=oid)
        )
    await db.flush()
    return ids


async def list_org_users(
    db: AsyncSession, org_id: uuid.UUID, page: int = 1, page_size: int = 50
) -> tuple[list[tuple[User, int]], int]:
    """Paginated org members with their real workspace counts.

    Returns ``([(user, workspace_count), ...], total)``. The workspace count is
    computed with the same outer-join the main /admin/users listing uses, so the
    AdminUserResponse field is accurate rather than a hardcoded 0.
    """
    org = await db.get(Organization, org_id)
    if org is None:
        raise OrgNotFound("Organization not found")
    total = (
        await db.execute(select(func.count()).where(User.organization_id == org_id))
    ).scalar_one()
    rows = (
        await db.execute(
            select(User, func.count(WorkspaceMembership.id).label("workspace_count"))
            .outerjoin(WorkspaceMembership, User.id == WorkspaceMembership.user_id)
            .where(User.organization_id == org_id)
            .group_by(User.id)
            .order_by(User.email)
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
    ).all()
    return [(user, count) for user, count in rows], total
