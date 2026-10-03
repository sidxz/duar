"""One-time workspace join links (self-serve mode).

A code is shown to the inviter once; only its SHA-256 is stored. Redemption is
an atomic conditional UPDATE so a code can be claimed exactly once, and the
claim also requires the inviter to STILL be owner/admin of the workspace — a
removed admin's pre-minted links are dead. Every failure mode collapses to one
generic ``InvitationInvalid`` so nothing about the invitation is disclosed.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models.invitation import WorkspaceInvitation
from src.models.user import User
from src.models.workspace import Workspace, WorkspaceMembership
from src.services import organization_service
from src.services.workspace_service import SelfServeDisabled

INVITATION_TTL = timedelta(days=7)
MAX_PENDING_PER_WORKSPACE = 50
_ROLES = ("admin", "editor", "viewer")
_ADMIN_ROLES = ("owner", "admin")


class InvitationInvalid(Exception):
    """Unknown, used, expired, revoked, wrong email, or inviter lost standing."""


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _normalize_email(value: str) -> str:
    email = value.strip().lower()
    if email.count("@") != 1 or len(email) > 254 or any(c.isspace() for c in email):
        raise ValueError("Invalid email address")
    local, domain = email.split("@")
    if not local or "." not in domain:
        raise ValueError("Invalid email address")
    return email


def _pending(now: datetime):
    inv = WorkspaceInvitation
    return (inv.accepted_at.is_(None), inv.revoked_at.is_(None), inv.expires_at > now)


def _inviter_standing():
    inv, mem = WorkspaceInvitation, WorkspaceMembership
    return exists(
        select(mem.user_id)
        .join(User, User.id == mem.user_id)
        .where(
            mem.workspace_id == inv.workspace_id,
            mem.user_id == inv.created_by,
            mem.role.in_(_ADMIN_ROLES),
            User.is_active.is_(True),
        )
    )


def _require_enabled() -> None:
    if not settings.self_serve_enabled:
        raise SelfServeDisabled()


async def create(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    role: str,
    created_by: uuid.UUID,
    actor_role: str,
    email: str | None = None,
    now: datetime | None = None,
) -> tuple[WorkspaceInvitation, str]:
    _require_enabled()
    if actor_role not in _ADMIN_ROLES:
        raise ValueError("Only workspace owners and admins can invite")
    if role not in _ROLES:
        raise ValueError("Invitations can grant viewer, editor, or admin only")
    locked = _normalize_email(email) if email else None
    now = now or datetime.now(UTC)
    # Serialize per-workspace so concurrent creates cannot exceed the pending cap.
    await db.execute(
        select(Workspace.id).where(Workspace.id == workspace_id).with_for_update()
    )
    pending = await db.scalar(
        select(func.count())
        .select_from(WorkspaceInvitation)
        .where(WorkspaceInvitation.workspace_id == workspace_id, *_pending(now))
    )
    if int(pending or 0) >= MAX_PENDING_PER_WORKSPACE:
        raise ValueError("Too many pending invitations for this workspace")
    code = secrets.token_urlsafe(32)
    inv = WorkspaceInvitation(
        workspace_id=workspace_id,
        code_hash=_hash(code),
        email=locked,
        role=role,
        created_by=created_by,
        expires_at=now + INVITATION_TTL,
    )
    db.add(inv)
    await db.commit()
    return inv, code


async def list_for_workspace(
    db: AsyncSession, workspace_id: uuid.UUID, now: datetime | None = None
) -> list[WorkspaceInvitation]:
    now = now or datetime.now(UTC)
    stmt = (
        select(WorkspaceInvitation)
        .where(WorkspaceInvitation.workspace_id == workspace_id, *_pending(now))
        .order_by(WorkspaceInvitation.created_at)
    )
    return list((await db.execute(stmt)).scalars().all())


async def revoke(
    db: AsyncSession,
    invitation_id: uuid.UUID,
    *,
    actor_id: uuid.UUID,
    now: datetime | None = None,
) -> WorkspaceInvitation:
    _require_enabled()
    now = now or datetime.now(UTC)
    inv = await db.scalar(
        select(WorkspaceInvitation).where(
            WorkspaceInvitation.id == invitation_id, *_pending(now)
        )
    )
    if inv is None:
        raise InvitationInvalid()
    actor_role = await db.scalar(
        select(WorkspaceMembership.role).where(
            WorkspaceMembership.workspace_id == inv.workspace_id,
            WorkspaceMembership.user_id == actor_id,
        )
    )
    if actor_role not in _ADMIN_ROLES:
        raise PermissionError("Only workspace owners and admins can revoke")
    inv.revoked_at = now
    await db.commit()
    return inv


async def peek(
    db: AsyncSession, code: str, now: datetime | None = None
) -> WorkspaceInvitation | None:
    """Read-only lookup with the same predicates the claim uses."""
    now = now or datetime.now(UTC)
    stmt = select(WorkspaceInvitation).where(
        WorkspaceInvitation.code_hash == _hash(code),
        *_pending(now),
        _inviter_standing(),
    )
    return await db.scalar(stmt)


async def redeem(
    db: AsyncSession, user: User, code: str, now: datetime | None = None
) -> WorkspaceInvitation:
    """Claim ``code`` for ``user`` and create the membership.

    Raises ``InvitationInvalid`` (generic) or the org gate's ``ValueError``
    (about the user, safe to show; the invitation is NOT consumed).
    """
    _require_enabled()
    now = now or datetime.now(UTC)
    inv = await peek(db, code, now=now)
    if inv is None:
        raise InvitationInvalid()
    if inv.email and inv.email != user.email.strip().lower():
        raise InvitationInvalid()
    await organization_service.assert_user_allowed_in_workspace(
        db, user, inv.workspace_id
    )
    result = await db.execute(
        update(WorkspaceInvitation)
        .where(WorkspaceInvitation.id == inv.id, *_pending(now), _inviter_standing())
        .values(accepted_by=user.id, accepted_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise InvitationInvalid()
    already = await db.scalar(
        select(WorkspaceMembership.id).where(
            WorkspaceMembership.workspace_id == inv.workspace_id,
            WorkspaceMembership.user_id == user.id,
        )
    )
    if already is None:
        db.add(
            WorkspaceMembership(
                workspace_id=inv.workspace_id, user_id=user.id, role=inv.role
            )
        )
    await db.commit()
    return inv
