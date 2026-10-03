import csv
import io
import uuid

from sqlalchemy import Text, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.group import Group
from src.models.permission import ResourcePermission, ResourceShare
from src.models.user import User
from src.models.workspace import Workspace, WorkspaceMembership
from src.schemas.validators import strip_html
from src.services import activity_service, organization_service, token_service


async def get_stats(db: AsyncSession) -> dict:
    total_users = await db.scalar(select(func.count(User.id)))
    total_workspaces = await db.scalar(select(func.count(Workspace.id)))
    total_groups = await db.scalar(select(func.count(Group.id)))
    total_resources = await db.scalar(select(func.count(ResourcePermission.id)))
    active_users = await db.scalar(
        select(func.count(User.id)).where(User.is_active.is_(True))
    )
    inactive_users = (total_users or 0) - (active_users or 0)

    recent_stmt = (
        select(User, func.count(WorkspaceMembership.id).label("workspace_count"))
        .outerjoin(WorkspaceMembership, User.id == WorkspaceMembership.user_id)
        .group_by(User.id)
        .order_by(User.created_at.desc())
        .limit(5)
    )
    result = await db.execute(recent_stmt)
    recent_users = [
        {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "avatar_url": user.avatar_url,
            "is_active": user.is_active,
            "is_admin": user.is_admin,
            "created_at": user.created_at,
            "workspace_count": count,
        }
        for user, count in result.all()
    ]

    # Top 5 workspaces by member count
    top_ws_stmt = (
        select(Workspace, func.count(WorkspaceMembership.id).label("member_count"))
        .outerjoin(
            WorkspaceMembership, Workspace.id == WorkspaceMembership.workspace_id
        )
        .group_by(Workspace.id)
        .order_by(func.count(WorkspaceMembership.id).desc())
        .limit(5)
    )
    top_ws_result = await db.execute(top_ws_stmt)
    top_workspaces = [
        {"id": ws.id, "name": ws.name, "slug": ws.slug, "member_count": count}
        for ws, count in top_ws_result.all()
    ]

    return {
        "total_users": total_users or 0,
        "total_workspaces": total_workspaces or 0,
        "total_groups": total_groups or 0,
        "total_resources": total_resources or 0,
        "active_users": active_users or 0,
        "inactive_users": inactive_users,
        "recent_users": recent_users,
        "top_workspaces": top_workspaces,
    }


async def list_users(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
) -> dict:
    base_query = (
        select(User, func.count(WorkspaceMembership.id).label("workspace_count"))
        .outerjoin(WorkspaceMembership, User.id == WorkspaceMembership.user_id)
        .group_by(User.id)
    )

    if search:
        base_query = base_query.where(
            User.email.icontains(search, autoescape=True)
            | User.name.icontains(search, autoescape=True)
        )

    # Count total
    count_query = select(func.count()).select_from(User)
    if search:
        count_query = count_query.where(
            User.email.icontains(search, autoescape=True)
            | User.name.icontains(search, autoescape=True)
        )
    total = await db.scalar(count_query) or 0

    # Fetch page
    stmt = (
        base_query.order_by(User.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)

    items = [
        {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "avatar_url": user.avatar_url,
            "is_active": user.is_active,
            "is_admin": user.is_admin,
            "created_at": user.created_at,
            "workspace_count": count,
        }
        for user, count in result.all()
    ]

    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def get_user_detail(db: AsyncSession, user_id: uuid.UUID) -> dict | None:
    stmt = (
        select(User)
        .options(selectinload(User.social_accounts))
        .where(User.id == user_id)
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        return None

    # Get memberships with workspace info
    membership_stmt = (
        select(WorkspaceMembership, Workspace)
        .join(Workspace, WorkspaceMembership.workspace_id == Workspace.id)
        .where(WorkspaceMembership.user_id == user_id)
        .order_by(WorkspaceMembership.joined_at)
    )
    membership_result = await db.execute(membership_stmt)

    memberships = [
        {
            "workspace_id": m.workspace_id,
            "workspace_name": w.name,
            "workspace_slug": w.slug,
            "role": m.role,
            "joined_at": m.joined_at,
        }
        for m, w in membership_result.all()
    ]

    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "avatar_url": user.avatar_url,
        "is_active": user.is_active,
        "is_admin": user.is_admin,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
        "social_accounts": [
            {
                "id": sa.id,
                "provider": sa.provider,
                "provider_user_id": sa.provider_user_id,
            }
            for sa in user.social_accounts
        ],
        "memberships": memberships,
    }


async def update_user(
    db: AsyncSession,
    user_id: uuid.UUID,
    name: str | None = None,
    is_active: bool | None = None,
    is_admin: bool | None = None,
) -> tuple[User | None, list[str]]:
    """Apply the changes and return (user, audit actions for what ACTUALLY
    changed). Flushes but does not commit — the caller commits mutation and
    audit rows in ONE transaction, so a privilege change can never land
    unaudited."""
    user = await db.get(User, user_id)
    if not user:
        return None, []
    actions: list[str] = []
    revoke = False
    deactivated = False
    activated = False
    if name is not None and name != user.name:
        user.name = name
        actions.append("user_updated")
    if is_active is not None and is_active != user.is_active:
        user.is_active = is_active
        if not is_active:
            revoke = True
            deactivated = True
            actions.append("user_deactivated")
        else:
            activated = True
            actions.append("user_activated")
    if is_admin is not None and is_admin != user.is_admin:
        user.is_admin = is_admin
        if not is_admin:
            revoke = True
            actions.append("user_demoted_admin")
        else:
            actions.append("user_promoted_admin")
    await db.flush()
    # Redis side effects before commit: if the commit later fails, we've
    # over-revoked (safe direction), never under-revoked.
    if revoke:
        await token_service.revoke_all_user_tokens(str(user_id))
    if deactivated:
        await token_service.mark_user_deactivated(str(user_id))
    elif activated:
        await token_service.mark_user_activated(str(user_id))
    return user, actions


async def list_workspaces(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
) -> dict:
    base_query = (
        select(Workspace, func.count(WorkspaceMembership.id).label("member_count"))
        .outerjoin(
            WorkspaceMembership, Workspace.id == WorkspaceMembership.workspace_id
        )
        .group_by(Workspace.id)
    )

    if search:
        base_query = base_query.where(
            Workspace.name.icontains(search, autoescape=True)
            | Workspace.slug.icontains(search, autoescape=True)
        )

    count_query = select(func.count()).select_from(Workspace)
    if search:
        count_query = count_query.where(
            Workspace.name.icontains(search, autoescape=True)
            | Workspace.slug.icontains(search, autoescape=True)
        )
    total = await db.scalar(count_query) or 0

    stmt = (
        base_query.order_by(Workspace.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)

    items = [
        {
            "id": ws.id,
            "slug": ws.slug,
            "name": ws.name,
            "description": ws.description,
            "created_by": ws.created_by,
            "created_at": ws.created_at,
            "member_count": count,
        }
        for ws, count in result.all()
    ]

    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def get_workspace_detail(
    db: AsyncSession, workspace_id: uuid.UUID
) -> dict | None:
    workspace = await db.get(Workspace, workspace_id)
    if not workspace:
        return None

    member_count = await db.scalar(
        select(func.count(WorkspaceMembership.id)).where(
            WorkspaceMembership.workspace_id == workspace_id
        )
    )
    group_count = await db.scalar(
        select(func.count(Group.id)).where(Group.workspace_id == workspace_id)
    )

    return {
        "id": workspace.id,
        "slug": workspace.slug,
        "name": workspace.name,
        "description": workspace.description,
        "created_by": workspace.created_by,
        "created_at": workspace.created_at,
        "member_count": member_count or 0,
        "group_count": group_count or 0,
    }


async def list_all_workspaces(db: AsyncSession) -> list[dict]:
    stmt = select(Workspace).order_by(Workspace.name)
    result = await db.execute(stmt)
    return [
        {"id": ws.id, "name": ws.name, "slug": ws.slug} for ws in result.scalars().all()
    ]


async def list_permissions(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    workspace_id: uuid.UUID | None = None,
    service_name: str | None = None,
    resource_id: str | None = None,
    owner: str | None = None,
    sort_by: str | None = None,
    sort_order: str = "desc",
) -> dict:
    share_count_col = func.count(ResourceShare.id).label("share_count")
    base_query = (
        select(
            ResourcePermission,
            User.email.label("owner_email"),
            share_count_col,
        )
        .outerjoin(User, ResourcePermission.owner_id == User.id)
        .outerjoin(
            ResourceShare, ResourcePermission.id == ResourceShare.resource_permission_id
        )
        .group_by(ResourcePermission.id, User.email)
    )

    count_query = (
        select(func.count())
        .select_from(ResourcePermission)
        .outerjoin(User, ResourcePermission.owner_id == User.id)
    )

    filters = []
    if workspace_id:
        filters.append(ResourcePermission.workspace_id == workspace_id)
    if service_name:
        filters.append(ResourcePermission.service_name == service_name)
    if resource_id:
        filters.append(
            cast(ResourcePermission.resource_id, Text).istartswith(
                resource_id, autoescape=True
            )
        )
    if owner:
        filters.append(User.email.icontains(owner, autoescape=True))

    for f in filters:
        base_query = base_query.where(f)
        count_query = count_query.where(f)

    total = await db.scalar(count_query) or 0

    if sort_by == "shares":
        order_col = share_count_col
    else:
        order_col = ResourcePermission.created_at

    order_expr = order_col.asc() if sort_order == "asc" else order_col.desc()

    stmt = (
        base_query.order_by(order_expr).offset((page - 1) * page_size).limit(page_size)
    )
    result = await db.execute(stmt)

    items = [
        {
            "id": perm.id,
            "service_name": perm.service_name,
            "resource_type": perm.resource_type,
            "resource_id": perm.resource_id,
            "workspace_id": perm.workspace_id,
            "owner_id": perm.owner_id,
            "owner_email": owner_email,
            "visibility": perm.visibility,
            "created_at": perm.created_at,
            "share_count": share_count,
        }
        for perm, owner_email, share_count in result.all()
    ]

    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def get_permission_detail(
    db: AsyncSession, permission_id: uuid.UUID
) -> dict | None:
    stmt = (
        select(ResourcePermission, User.email.label("owner_email"))
        .outerjoin(User, ResourcePermission.owner_id == User.id)
        .where(ResourcePermission.id == permission_id)
    )
    result = await db.execute(stmt)
    row = result.one_or_none()
    if not row:
        return None
    perm, owner_email = row

    # Load shares
    shares_stmt = select(ResourceShare).where(
        ResourceShare.resource_permission_id == permission_id
    )
    shares_result = await db.execute(shares_stmt)
    shares = [
        {
            "id": s.id,
            "grantee_type": s.grantee_type,
            "grantee_id": s.grantee_id,
            "permission": s.permission,
            "granted_by": s.granted_by,
            "granted_at": s.granted_at,
        }
        for s in shares_result.scalars().all()
    ]

    return {
        "id": perm.id,
        "service_name": perm.service_name,
        "resource_type": perm.resource_type,
        "resource_id": perm.resource_id,
        "workspace_id": perm.workspace_id,
        "owner_id": perm.owner_id,
        "owner_email": owner_email,
        "visibility": perm.visibility,
        "created_at": perm.created_at,
        "share_count": len(shares),
        "shares": shares,
    }


async def add_user_to_workspace(
    db: AsyncSession,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    role: str,
    actor_id: uuid.UUID | None = None,
) -> WorkspaceMembership:
    user = await db.get(User, user_id)
    if user is None:
        raise ValueError("User not found")
    # Same allowed-orgs gate the invite path enforces, so the admin panel cannot
    # create a membership the user could never redeem (token issuance would 403).
    await organization_service.assert_user_allowed_in_workspace(db, user, workspace_id)
    membership = WorkspaceMembership(
        workspace_id=workspace_id, user_id=user_id, role=role
    )
    db.add(membership)
    await db.flush()
    await activity_service.log_activity(
        db,
        action="member_invited",
        target_type="user",
        target_id=user_id,
        actor_id=actor_id,
        workspace_id=workspace_id,
        detail={"role": role},
    )
    await db.commit()
    return membership


async def bulk_update_status(
    db: AsyncSession,
    user_ids: list[uuid.UUID],
    is_active: bool,
) -> int:
    from sqlalchemy import update

    result = await db.execute(
        update(User).where(User.id.in_(user_ids)).values(is_active=is_active)
    )
    await db.commit()
    if not is_active:
        # Revoke tokens and mark deactivated so access is cut immediately
        for uid in user_ids:
            await token_service.revoke_all_user_tokens(str(uid))
            await token_service.mark_user_deactivated(str(uid))
    else:
        # Clear deactivation flags for reactivated users
        for uid in user_ids:
            await token_service.mark_user_activated(str(uid))
    return result.rowcount  # type: ignore[return-value]


def parse_csv(content: str) -> dict:
    reader = csv.DictReader(io.StringIO(content))
    rows = []
    valid_count = 0
    error_count = 0

    required = {"email", "name", "workspace_slug", "role"}
    if reader.fieldnames:
        missing = required - set(reader.fieldnames)
        if missing:
            return {
                "rows": [],
                "valid_count": 0,
                "error_count": 1,
            }

    for row in reader:
        email = (row.get("email") or "").strip()
        name = (row.get("name") or "").strip()
        workspace_slug = (row.get("workspace_slug") or "").strip()
        role = (row.get("role") or "viewer").strip()

        error = None
        if not email:
            error = "Missing email"
        elif not name:
            error = "Missing name"
        elif not workspace_slug:
            error = "Missing workspace_slug"
        elif role not in ("owner", "admin", "editor", "viewer"):
            error = f"Invalid role: {role}"

        if error:
            error_count += 1
        else:
            valid_count += 1

        rows.append(
            {
                "email": email,
                "name": name,
                "workspace_slug": workspace_slug,
                "role": role,
                "error": error,
            }
        )

    return {"rows": rows, "valid_count": valid_count, "error_count": error_count}


async def execute_import(
    db: AsyncSession,
    rows: list[dict],
    actor_id: uuid.UUID | None = None,
) -> dict:
    users_created = 0
    memberships_added = 0
    errors: list[str] = []

    for row in rows:
        if row.get("error"):
            continue

        email = row["email"]
        name = strip_html(row["name"])
        workspace_slug = row["workspace_slug"]
        role = row["role"]

        try:
            # Each row runs inside a SAVEPOINT: a DB-level failure (e.g. a
            # TOCTOU unique violation at flush) rolls back only this row —
            # without it the session is left poisoned and every later
            # db.execute raises PendingRollbackError, 500ing the whole import.
            row_user_created = False
            async with db.begin_nested():
                # Resolve the workspace first — a missing workspace (or any later
                # rejection) must NOT leave a half-created orphan user behind.
                ws_result = await db.execute(
                    select(Workspace).where(Workspace.slug == workspace_slug)
                )
                workspace = ws_result.scalar_one_or_none()
                if not workspace:
                    errors.append(f"Workspace '{workspace_slug}' not found for {email}")
                    continue

                # Look up — but do NOT yet create — the pre-provisioned user (no
                # SocialAccount; they must still authenticate via an external IdP before
                # they can get tokens).
                user_result = await db.execute(select(User).where(User.email == email))
                user = user_result.scalar_one_or_none()

                if user is not None:
                    existing = await db.execute(
                        select(WorkspaceMembership).where(
                            WorkspaceMembership.workspace_id == workspace.id,
                            WorkspaceMembership.user_id == user.id,
                        )
                    )
                    if existing.scalar_one_or_none():
                        errors.append(
                            f"{email} already in workspace '{workspace_slug}'"
                        )
                        continue

                # Enforce the workspace's allowed-orgs gate BEFORE creating anything, so
                # a rejected row never persists an orphan account or inflates the created
                # count. A not-yet-created account is gated via a transient stand-in
                # carrying the email the effective-org resolution keys on.
                gate_user = user if user is not None else User(email=email, name=name)
                try:
                    await organization_service.assert_user_allowed_in_workspace(
                        db, gate_user, workspace.id
                    )
                except ValueError as e:
                    errors.append(f"{email}: {e}")
                    continue

                # All gates passed — now it is safe to create the user. Stamp the org
                # resolved from their email domain so org user-counts/listings reflect
                # the intended tenancy immediately (first IdP sign-in refreshes it).
                if user is None:
                    org = await organization_service.resolve_organization(db, email)
                    user = User(
                        email=email,
                        name=name,
                        organization_id=org.id if org else None,
                    )
                    db.add(user)
                    await db.flush()
                    row_user_created = True

                membership = WorkspaceMembership(
                    workspace_id=workspace.id, user_id=user.id, role=role
                )
                db.add(membership)
                # Flush inside the savepoint so a row-level DB error surfaces
                # here (and rolls back to the savepoint) instead of at commit.
                await db.flush()

            # Count only after the savepoint committed — a rolled-back row
            # must not inflate the totals.
            if row_user_created:
                users_created += 1
            memberships_added += 1

        except Exception as e:
            errors.append(f"Error processing {email}: {str(e)}")

    # Always audited — an import where every row errored is still an admin
    # feeding a user CSV into the system and must leave a record.
    await activity_service.log_activity(
        db,
        action="batch_import",
        target_type="system",
        target_id=uuid.uuid4(),
        actor_id=actor_id,
        detail={
            "users_created": users_created,
            "memberships_added": memberships_added,
            "errors": len(errors),
        },
    )

    await db.commit()
    return {
        "users_created": users_created,
        "memberships_added": memberships_added,
        "errors": errors,
    }
