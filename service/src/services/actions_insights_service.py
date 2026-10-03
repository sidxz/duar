"""RBAC action-usage insights: usage aggregates + role-mining anti-joins.

Reads the ``action_usage`` daily rollup (allowed checks) and ``action_denied``
activity events (denied checks). Role-mining sections cross-reference the
grant tables (user_roles + group_roles⋈group_memberships) to surface
granted-but-never-used pairs and roles nobody exercises.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.activity import ActivityLog
from src.models.group import GroupMembership
from src.models.role import (
    ActionUsage,
    GroupRole,
    Role,
    RoleAction,
    ServiceAction,
    UserRole,
)
from src.models.user import User
from src.models.workspace import Workspace


_DORMANT_LIMIT = 50


def _day_iso(d) -> str:
    """date-or-string day → ISO string (func.date returns str on SQLite)."""
    return d.isoformat() if isinstance(d, date) else str(d)[:10]


async def actions_insights(
    db: AsyncSession, days: int = 30, workspace_id: uuid.UUID | None = None
) -> dict:
    now = datetime.now(UTC)
    since = (now - timedelta(days=days)).date()

    def scoped(stmt, col=ActionUsage.workspace_id):
        return stmt.where(col == workspace_id) if workspace_id else stmt

    total = func.sum(ActionUsage.count).label("count")

    top_actions = (
        await db.execute(
            scoped(
                select(ActionUsage.service_name, ActionUsage.action, total)
                .where(ActionUsage.day >= since)
                .group_by(ActionUsage.service_name, ActionUsage.action)
                .order_by(total.desc())
                .limit(20)
            )
        )
    ).all()

    by_service = (
        await db.execute(
            scoped(
                select(ActionUsage.service_name, total)
                .where(ActionUsage.day >= since)
                .group_by(ActionUsage.service_name)
                .order_by(total.desc())
            )
        )
    ).all()

    top_users = (
        await db.execute(
            scoped(
                select(ActionUsage.user_id, User.email, User.name, total)
                .join(User, User.id == ActionUsage.user_id)
                .where(ActionUsage.day >= since)
                .group_by(ActionUsage.user_id, User.email, User.name)
                .order_by(total.desc())
                .limit(20)
            )
        )
    ).all()

    allowed_rows = (
        await db.execute(
            scoped(
                select(ActionUsage.day, func.sum(ActionUsage.count))
                .where(ActionUsage.day >= since)
                .group_by(ActionUsage.day)
            )
        )
    ).all()
    denied_day = func.date(ActivityLog.created_at).label("day")
    denied_rows = (
        await db.execute(
            scoped(
                select(denied_day, func.count())
                .where(
                    ActivityLog.action == "action_denied",
                    ActivityLog.created_at >= now - timedelta(days=days),
                )
                .group_by(denied_day),
                col=ActivityLog.workspace_id,
            )
        )
    ).all()

    trend: dict[str, dict] = {}
    for d, n in allowed_rows:
        trend.setdefault(_day_iso(d), {"allowed": 0, "denied": 0})["allowed"] = n
    for d, n in denied_rows:
        trend.setdefault(_day_iso(d), {"allowed": 0, "denied": 0})["denied"] = n

    data_since = (
        await db.execute(select(func.min(ActionUsage.day)))
    ).scalar_one_or_none()

    # ── dormant grants: granted (user, service, action) pairs w/ no usage ──
    direct = (
        select(
            UserRole.user_id.label("user_id"),
            ServiceAction.service_name.label("service_name"),
            ServiceAction.action.label("action"),
            Role.name.label("role_name"),
            Role.workspace_id.label("workspace_id"),
        )
        .select_from(Role)
        .join(UserRole, UserRole.role_id == Role.id)
        .join(RoleAction, RoleAction.role_id == Role.id)
        .join(ServiceAction, RoleAction.service_action_id == ServiceAction.id)
    )
    via_group = (
        select(
            GroupMembership.user_id.label("user_id"),
            ServiceAction.service_name.label("service_name"),
            ServiceAction.action.label("action"),
            Role.name.label("role_name"),
            Role.workspace_id.label("workspace_id"),
        )
        .select_from(Role)
        .join(GroupRole, GroupRole.role_id == Role.id)
        .join(GroupMembership, GroupMembership.group_id == GroupRole.group_id)
        .join(RoleAction, RoleAction.role_id == Role.id)
        .join(ServiceAction, RoleAction.service_action_id == ServiceAction.id)
    )
    if workspace_id:
        direct = direct.where(Role.workspace_id == workspace_id)
        via_group = via_group.where(Role.workspace_id == workspace_id)
    granted = direct.union(via_group).subquery("granted")

    used = (
        select(1)
        .where(
            ActionUsage.user_id == granted.c.user_id,
            ActionUsage.workspace_id == granted.c.workspace_id,
            ActionUsage.service_name == granted.c.service_name,
            ActionUsage.action == granted.c.action,
            ActionUsage.day >= since,
        )
        .exists()
    )
    dormant_base = (
        select(
            granted.c.user_id,
            User.email,
            User.name,
            granted.c.service_name,
            granted.c.action,
            granted.c.role_name,
            granted.c.workspace_id,
            Workspace.name.label("workspace_name"),
        )
        .join(User, User.id == granted.c.user_id)
        .join(Workspace, Workspace.id == granted.c.workspace_id)
        .where(~used)
    )
    dormant_total = (
        await db.execute(select(func.count()).select_from(dormant_base.subquery()))
    ).scalar_one()
    dormant_rows = (
        await db.execute(
            dormant_base.order_by(
                granted.c.service_name, granted.c.action, User.email
            ).limit(_DORMANT_LIMIT)
        )
    ).all()

    # ── unused roles: no assignee exercised any of the role's actions ──
    direct_use = (
        select(1)
        .select_from(UserRole)
        .join(RoleAction, RoleAction.role_id == UserRole.role_id)
        .join(ServiceAction, ServiceAction.id == RoleAction.service_action_id)
        .join(
            ActionUsage,
            and_(
                ActionUsage.user_id == UserRole.user_id,
                ActionUsage.workspace_id == Role.workspace_id,
                ActionUsage.service_name == ServiceAction.service_name,
                ActionUsage.action == ServiceAction.action,
                ActionUsage.day >= since,
            ),
        )
        .where(UserRole.role_id == Role.id)
    )
    group_use = (
        select(1)
        .select_from(GroupRole)
        .join(GroupMembership, GroupMembership.group_id == GroupRole.group_id)
        .join(RoleAction, RoleAction.role_id == GroupRole.role_id)
        .join(ServiceAction, ServiceAction.id == RoleAction.service_action_id)
        .join(
            ActionUsage,
            and_(
                ActionUsage.user_id == GroupMembership.user_id,
                ActionUsage.workspace_id == Role.workspace_id,
                ActionUsage.service_name == ServiceAction.service_name,
                ActionUsage.action == ServiceAction.action,
                ActionUsage.day >= since,
            ),
        )
        .where(GroupRole.role_id == Role.id)
    )
    direct_assignees = (
        select(func.count()).where(UserRole.role_id == Role.id).scalar_subquery()
    )
    group_assignees = (
        select(func.count())
        .select_from(GroupRole)
        .join(GroupMembership, GroupMembership.group_id == GroupRole.group_id)
        .where(GroupRole.role_id == Role.id)
        .scalar_subquery()
    )
    unused_stmt = (
        select(
            Role.id,
            Role.name,
            Role.workspace_id,
            Workspace.name.label("workspace_name"),
            (direct_assignees + group_assignees).label("assignees"),
        )
        .join(Workspace, Workspace.id == Role.workspace_id)
        .where(~exists(direct_use), ~exists(group_use))
        .order_by(Workspace.name, Role.name)
    )
    if workspace_id:
        unused_stmt = unused_stmt.where(Role.workspace_id == workspace_id)
    unused_rows = (await db.execute(unused_stmt)).all()

    return {
        "days": days,
        "since": since.isoformat(),
        "data_since": _day_iso(data_since) if data_since else None,
        "top_actions": [
            {"service_name": s, "action": a, "count": c} for s, a, c in top_actions
        ],
        "by_service": [{"service_name": s, "count": c} for s, c in by_service],
        "top_users": [
            {"user_id": str(u), "email": e, "name": n, "count": c}
            for u, e, n, c in top_users
        ],
        "trend": [{"day": d, **v} for d, v in sorted(trend.items())],
        "dormant_grants": {
            "total": dormant_total,
            "items": [
                {
                    "user_id": str(r.user_id),
                    "email": r.email,
                    "name": r.name,
                    "service_name": r.service_name,
                    "action": r.action,
                    "role_name": r.role_name,
                    "workspace_id": str(r.workspace_id),
                    "workspace_name": r.workspace_name,
                }
                for r in dormant_rows
            ],
        },
        "unused_roles": [
            {
                "id": str(r.id),
                "name": r.name,
                "workspace_id": str(r.workspace_id),
                "workspace_name": r.workspace_name,
                "assignees": r.assignees,
                "no_assignees": r.assignees == 0,
            }
            for r in unused_rows
        ],
    }
