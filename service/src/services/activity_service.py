import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.logging_events import log_audit
from src.models.activity import ActivityLog
from src.models.client_app import ClientApp
from src.models.group import Group
from src.models.organization import Organization
from src.models.realm import Realm
from src.models.role import Role
from src.models.service_app import ServiceApp
from src.models.user import User
from src.models.workspace import Workspace

# target_type → model with a human-readable name; types not listed (system,
# resource_permission, export, …) fall back to the raw id in the UI.
_TARGET_MODELS = {
    "user": User,
    "workspace": Workspace,
    "group": Group,
    "role": Role,
    "realm": Realm,
    "organization": Organization,
    "client_app": ClientApp,
    "service_app": ServiceApp,
}


async def _attach_target_labels(db: AsyncSession, items: list[dict]) -> None:
    """Resolve target_id → display name in one query per target type on the page.
    Deleted targets simply resolve to None (UI falls back to the id)."""
    by_type: dict[str, set[uuid.UUID]] = {}
    for it in items:
        if it["target_type"] in _TARGET_MODELS and it["target_id"] is not None:
            by_type.setdefault(it["target_type"], set()).add(it["target_id"])
    labels: dict[tuple[str, uuid.UUID], str] = {}
    for tt, ids in by_type.items():
        model = _TARGET_MODELS[tt]
        rows = (await db.execute(select(model).where(model.id.in_(ids)))).scalars()
        for row in rows:
            labels[(tt, row.id)] = row.name or getattr(row, "email", None)
    for it in items:
        it["target_label"] = labels.get((it["target_type"], it["target_id"]))


def _emit_audit(payload: dict) -> None:
    """Emit one audit.activity log event. The DB ActivityLog row is the
    system-of-record; a log-stream emit failure must never break the audit."""
    try:
        log_audit(**payload)
    except Exception:
        pass


def _register_commit_flush(sync_session) -> None:
    """Install (once per session) hooks that drain pending audit events on commit
    and discard them on rollback — so a rolled-back transaction never emits a
    phantom audit.activity event for an action the DB never durably recorded."""
    if sync_session.info.get("_audit_hook_installed"):
        return
    sync_session.info["_audit_hook_installed"] = True

    @event.listens_for(sync_session, "after_commit")
    def _on_commit(session):
        for payload in session.info.pop("pending_audit", []):
            _emit_audit(payload)

    @event.listens_for(sync_session, "after_rollback")
    def _on_rollback(session):
        session.info.pop("pending_audit", None)


async def log_activity(
    db: AsyncSession,
    action: str,
    target_type: str,
    target_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
    workspace_id: uuid.UUID | None = None,
    detail: dict | None = None,
) -> ActivityLog:
    entry = ActivityLog(
        action=action,
        actor_id=actor_id,
        target_type=target_type,
        target_id=target_id,
        workspace_id=workspace_id,
        detail=detail,
    )
    db.add(entry)
    await db.flush()

    payload = {
        "action": action,
        "target_type": target_type,
        "target_id": str(target_id) if target_id is not None else None,
        "actor": str(actor_id) if actor_id else "system",
        "workspace_id": str(workspace_id) if workspace_id else None,
        "detail": detail,
    }
    sync_session = getattr(db, "sync_session", None)
    if sync_session is not None:
        # Defer the log-stream emit to the transaction's commit so a later
        # rollback doesn't leave a phantom audit.activity event.
        _register_commit_flush(sync_session)
        sync_session.info.setdefault("pending_audit", []).append(payload)
    else:
        # No ORM session (unit tests / non-ORM callers) — emit immediately.
        _emit_audit(payload)
    return entry


async def list_paginated(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    action: str | None = None,
    target_type: str | None = None,
    workspace_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> dict:
    base = select(
        ActivityLog, User.name.label("actor_name"), User.email.label("actor_email")
    ).outerjoin(User, ActivityLog.actor_id == User.id)
    count_q = select(func.count()).select_from(ActivityLog)

    if action:
        base = base.where(ActivityLog.action == action)
        count_q = count_q.where(ActivityLog.action == action)
    if target_type:
        base = base.where(ActivityLog.target_type == target_type)
        count_q = count_q.where(ActivityLog.target_type == target_type)
    if workspace_id:
        base = base.where(ActivityLog.workspace_id == workspace_id)
        count_q = count_q.where(ActivityLog.workspace_id == workspace_id)
    if actor_id:
        base = base.where(ActivityLog.actor_id == actor_id)
        count_q = count_q.where(ActivityLog.actor_id == actor_id)
    if from_date:
        base = base.where(ActivityLog.created_at >= from_date)
        count_q = count_q.where(ActivityLog.created_at >= from_date)
    if to_date:
        base = base.where(ActivityLog.created_at <= to_date)
        count_q = count_q.where(ActivityLog.created_at <= to_date)

    total = await db.scalar(count_q) or 0

    stmt = (
        base.order_by(ActivityLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    items = [
        {
            "id": log.id,
            "action": log.action,
            "actor_id": log.actor_id,
            "actor_name": actor_name,
            "actor_email": actor_email,
            "target_type": log.target_type,
            "target_id": log.target_id,
            "workspace_id": log.workspace_id,
            "detail": log.detail,
            "created_at": log.created_at,
        }
        for log, actor_name, actor_email in result.all()
    ]
    await _attach_target_labels(db, items)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def daily_counts(db: AsyncSession, days: int = 30) -> list[dict]:
    """Per-day, per-action event counts for the last ``days`` days (dashboard charts)."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    # Reuse ONE labeled expression: date_trunc's unit binds as a parameter, so
    # repeating the call gives Postgres textually different expressions and a
    # GroupingError. Grouping by the label sidesteps it.
    day = func.date_trunc("day", ActivityLog.created_at).label("day")
    stmt = (
        select(day, ActivityLog.action, func.count().label("count"))
        .where(ActivityLog.created_at >= cutoff)
        .group_by(day, ActivityLog.action)
        .order_by(day)
    )
    result = await db.execute(stmt)
    return [
        {"day": day.date().isoformat(), "action": action, "count": count}
        for day, action, count in result.all()
    ]


async def list_recent(db: AsyncSession, limit: int = 20) -> list[dict]:
    stmt = (
        select(
            ActivityLog, User.name.label("actor_name"), User.email.label("actor_email")
        )
        .outerjoin(User, ActivityLog.actor_id == User.id)
        .order_by(ActivityLog.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    items = [
        {
            "id": log.id,
            "action": log.action,
            "actor_id": log.actor_id,
            "actor_name": actor_name,
            "actor_email": actor_email,
            "target_type": log.target_type,
            "target_id": log.target_id,
            "workspace_id": log.workspace_id,
            "detail": log.detail,
            "created_at": log.created_at,
        }
        for log, actor_name, actor_email in result.all()
    ]
    await _attach_target_labels(db, items)
    return items
