"""invitation_service: single-use claim, email lock, inviter standing, org gate, caps."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models.invitation import WorkspaceInvitation
from src.models.organization import Organization, WorkspaceAllowedOrganization
from src.models.user import User
from src.models.workspace import Workspace, WorkspaceMembership
from src.services import invitation_service as svc
from src.services.workspace_service import SelfServeDisabled
from tests.self_serve_fixtures import make_engine

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
# Not "...0001": that's all-digit hex, and SQLite gives the UUID column NUMERIC
# affinity (its type name has no INT/CHAR/TEXT/BLOB/REAL), silently coercing an
# all-digit 32-char UUID string to the integer 1 on storage — then crashing on
# readback. A trailing hex letter keeps the column TEXT-affinity in practice.
PUBLIC_ORG_ID = uuid.UUID("00000000-0000-0000-0000-00000000000a")


@pytest_asyncio.fixture
async def db():
    engine = await make_engine()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        session.add(
            Organization(
                id=PUBLIC_ORG_ID,
                slug="public",
                name="Public",
                is_public=True,
                enabled=True,
            )
        )
        await session.commit()
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    monkeypatch.setattr(settings, "self_serve_enabled", True)


async def _user(db, email) -> User:
    u = User(email=email, name=email.split("@")[0], organization_id=PUBLIC_ORG_ID)
    db.add(u)
    await db.flush()
    return u


async def _workspace(db, owner: User, admin_role="owner") -> Workspace:
    ws = Workspace(
        name="Acme", slug=f"acme-{uuid.uuid4().hex[:4]}", created_by=owner.id
    )
    db.add(ws)
    await db.flush()
    db.add(WorkspaceMembership(workspace_id=ws.id, user_id=owner.id, role=admin_role))
    await db.commit()
    return ws


async def _role(db, ws, user):
    return await db.scalar(
        select(WorkspaceMembership.role).where(
            WorkspaceMembership.workspace_id == ws.id,
            WorkspaceMembership.user_id == user.id,
        )
    )


@pytest.mark.asyncio
async def test_create_returns_code_once_and_stores_only_hash(db):
    owner = await _user(db, "o@example.com")
    ws = await _workspace(db, owner)
    inv, code = await svc.create(
        db,
        workspace_id=ws.id,
        role="editor",
        created_by=owner.id,
        actor_role="owner",
        now=NOW,
    )
    assert inv.code_hash == hashlib.sha256(code.encode()).hexdigest()
    assert code not in inv.code_hash and len(code) >= 43
    assert inv.expires_at == NOW + svc.INVITATION_TTL
    assert inv.email is None


@pytest.mark.asyncio
async def test_create_rejections(db):
    owner = await _user(db, "o@example.com")
    ws = await _workspace(db, owner)
    with pytest.raises(ValueError):
        await svc.create(
            db,
            workspace_id=ws.id,
            role="owner",
            created_by=owner.id,
            actor_role="owner",
            now=NOW,
        )
    with pytest.raises(ValueError):
        await svc.create(
            db,
            workspace_id=ws.id,
            role="viewer",
            created_by=owner.id,
            actor_role="editor",
            now=NOW,
        )
    with pytest.raises(ValueError):
        await svc.create(
            db,
            workspace_id=ws.id,
            role="viewer",
            created_by=owner.id,
            actor_role="owner",
            email="not-an-email",
            now=NOW,
        )


@pytest.mark.asyncio
async def test_create_flag_off(db, monkeypatch):
    monkeypatch.setattr(settings, "self_serve_enabled", False)
    owner = await _user(db, "o@example.com")
    ws = await _workspace(db, owner)
    with pytest.raises(SelfServeDisabled):
        await svc.create(
            db,
            workspace_id=ws.id,
            role="viewer",
            created_by=owner.id,
            actor_role="owner",
            now=NOW,
        )


@pytest.mark.asyncio
async def test_email_lock_normalized(db):
    owner = await _user(db, "o@example.com")
    ws = await _workspace(db, owner)
    inv, _ = await svc.create(
        db,
        workspace_id=ws.id,
        role="viewer",
        created_by=owner.id,
        actor_role="owner",
        email="  Alice@Example.COM ",
        now=NOW,
    )
    assert inv.email == "alice@example.com"


@pytest.mark.asyncio
async def test_pending_cap(db, monkeypatch):
    monkeypatch.setattr(svc, "MAX_PENDING_PER_WORKSPACE", 2)
    owner = await _user(db, "o@example.com")
    ws = await _workspace(db, owner)
    for _ in range(2):
        await svc.create(
            db,
            workspace_id=ws.id,
            role="viewer",
            created_by=owner.id,
            actor_role="owner",
            now=NOW,
        )
    with pytest.raises(ValueError):
        await svc.create(
            db,
            workspace_id=ws.id,
            role="viewer",
            created_by=owner.id,
            actor_role="owner",
            now=NOW,
        )


@pytest.mark.asyncio
async def test_redeem_happy_path_and_single_use(db):
    owner = await _user(db, "o@example.com")
    ws = await _workspace(db, owner)
    invitee = await _user(db, "i@example.com")
    _, code = await svc.create(
        db,
        workspace_id=ws.id,
        role="editor",
        created_by=owner.id,
        actor_role="owner",
        now=NOW,
    )
    inv = await svc.redeem(db, invitee, code, now=NOW + timedelta(days=1))
    assert inv.workspace_id == ws.id
    assert await _role(db, ws, invitee) == "editor"
    with pytest.raises(svc.InvitationInvalid):
        await svc.redeem(db, invitee, code, now=NOW + timedelta(days=1))


@pytest.mark.asyncio
async def test_redeem_generic_failures(db):
    owner = await _user(db, "o@example.com")
    ws = await _workspace(db, owner)
    invitee = await _user(db, "i@example.com")
    _, expired = await svc.create(
        db,
        workspace_id=ws.id,
        role="viewer",
        created_by=owner.id,
        actor_role="owner",
        now=NOW - timedelta(days=8),
    )
    _, locked = await svc.create(
        db,
        workspace_id=ws.id,
        role="viewer",
        created_by=owner.id,
        actor_role="owner",
        email="someone.else@example.com",
        now=NOW,
    )
    revoked_inv, revoked = await svc.create(
        db,
        workspace_id=ws.id,
        role="viewer",
        created_by=owner.id,
        actor_role="owner",
        now=NOW,
    )
    await svc.revoke(db, revoked_inv.id, actor_id=owner.id, now=NOW)
    for code in ("no-such-code", expired, locked, revoked):
        with pytest.raises(svc.InvitationInvalid):
            await svc.redeem(db, invitee, code, now=NOW)
    assert await _role(db, ws, invitee) is None


@pytest.mark.asyncio
async def test_redeem_email_lock_matches_case_insensitively(db):
    owner = await _user(db, "o@example.com")
    ws = await _workspace(db, owner)
    invitee = await _user(db, "Alice@Example.com")
    _, code = await svc.create(
        db,
        workspace_id=ws.id,
        role="viewer",
        created_by=owner.id,
        actor_role="owner",
        email="alice@example.com",
        now=NOW,
    )
    await svc.redeem(db, invitee, code, now=NOW)
    assert await _role(db, ws, invitee) == "viewer"


@pytest.mark.asyncio
async def test_redeem_dies_with_inviter_standing(db):
    owner = await _user(db, "o@example.com")
    ws = await _workspace(db, owner)
    admin = await _user(db, "a@example.com")
    db.add(WorkspaceMembership(workspace_id=ws.id, user_id=admin.id, role="admin"))
    await db.commit()
    _, code = await svc.create(
        db,
        workspace_id=ws.id,
        role="admin",
        created_by=admin.id,
        actor_role="admin",
        now=NOW,
    )
    # Owner removes the admin (simulate remove_member's effect).
    m = await db.scalar(
        select(WorkspaceMembership).where(WorkspaceMembership.user_id == admin.id)
    )
    await db.delete(m)
    await db.commit()
    with pytest.raises(svc.InvitationInvalid):
        await svc.redeem(db, admin, code, now=NOW)
    assert await _role(db, ws, admin) is None


@pytest.mark.asyncio
async def test_redeem_never_changes_existing_role(db):
    owner = await _user(db, "o@example.com")
    ws = await _workspace(db, owner)
    _, code = await svc.create(
        db,
        workspace_id=ws.id,
        role="viewer",
        created_by=owner.id,
        actor_role="owner",
        now=NOW,
    )
    inv = await svc.redeem(db, owner, code, now=NOW)  # owner redeems own invite
    assert await _role(db, ws, owner) == "owner"
    assert (
        await db.scalar(
            select(WorkspaceInvitation.accepted_by).where(
                WorkspaceInvitation.id == inv.id
            )
        )
        == owner.id
    )


@pytest.mark.asyncio
async def test_redeem_org_gate_does_not_consume(db):
    owner = await _user(db, "o@example.com")
    ws = await _workspace(db, owner)
    other_org = Organization(slug="acme", name="Acme", enabled=True)
    db.add(other_org)
    await db.flush()
    db.add(
        WorkspaceAllowedOrganization(workspace_id=ws.id, organization_id=other_org.id)
    )
    await db.commit()
    invitee = await _user(db, "i@example.com")  # public org, not allowed
    inv, code = await svc.create(
        db,
        workspace_id=ws.id,
        role="viewer",
        created_by=owner.id,
        actor_role="owner",
        now=NOW,
    )
    with pytest.raises(ValueError):
        await svc.redeem(db, invitee, code, now=NOW)
    assert (
        await db.scalar(
            select(WorkspaceInvitation.accepted_at).where(
                WorkspaceInvitation.id == inv.id
            )
        )
        is None
    )


@pytest.mark.asyncio
async def test_peek_list_and_revoke(db):
    owner = await _user(db, "o@example.com")
    ws = await _workspace(db, owner)
    inv, code = await svc.create(
        db,
        workspace_id=ws.id,
        role="viewer",
        created_by=owner.id,
        actor_role="owner",
        now=NOW,
    )
    assert (await svc.peek(db, code, now=NOW)).id == inv.id
    assert await svc.peek(db, "nope", now=NOW) is None
    assert [i.id for i in await svc.list_for_workspace(db, ws.id, now=NOW)] == [inv.id]
    outsider = await _user(db, "x@example.com")
    with pytest.raises(PermissionError):
        await svc.revoke(db, inv.id, actor_id=outsider.id, now=NOW)
    await svc.revoke(db, inv.id, actor_id=owner.id, now=NOW)
    assert await svc.list_for_workspace(db, ws.id, now=NOW) == []
    with pytest.raises(svc.InvitationInvalid):
        await svc.revoke(db, inv.id, actor_id=owner.id, now=NOW)


@pytest.mark.asyncio
async def test_claim_gate_rejects_already_accepted_after_peek(db, monkeypatch):
    """The atomic UPDATE's WHERE, not just peek's, must reject a stale claim.

    Simulates the race peek can't see: another redeemer wins the claim between
    peek's read and this caller's UPDATE. Force it by mutating the row behind
    the service's back, then feeding redeem a stale (pre-mutation) peek result
    so it falls through to the real conditional UPDATE.
    """
    owner = await _user(db, "o@example.com")
    ws = await _workspace(db, owner)
    invitee = await _user(db, "i@example.com")
    other = await _user(db, "other@example.com")
    inv, code = await svc.create(
        db,
        workspace_id=ws.id,
        role="viewer",
        created_by=owner.id,
        actor_role="owner",
        now=NOW,
    )
    inv.accepted_by = other.id
    inv.accepted_at = NOW
    await db.commit()
    monkeypatch.setattr(svc, "peek", AsyncMock(return_value=inv))
    with pytest.raises(svc.InvitationInvalid):
        await svc.redeem(db, invitee, code, now=NOW)
    assert await _role(db, ws, invitee) is None


@pytest.mark.asyncio
async def test_claim_gate_rejects_inviter_demoted_after_peek(db, monkeypatch):
    """Same gate, other half of the WHERE: inviter standing evaluated at claim time.

    The inviter loses admin standing between peek's read and this caller's
    UPDATE; the correlated EXISTS in the UPDATE's WHERE (not peek) must catch it.
    """
    owner = await _user(db, "o@example.com")
    ws = await _workspace(db, owner)
    admin = await _user(db, "a@example.com")
    db.add(WorkspaceMembership(workspace_id=ws.id, user_id=admin.id, role="admin"))
    await db.commit()
    invitee = await _user(db, "i@example.com")
    inv, code = await svc.create(
        db,
        workspace_id=ws.id,
        role="viewer",
        created_by=admin.id,
        actor_role="admin",
        now=NOW,
    )
    m = await db.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == ws.id,
            WorkspaceMembership.user_id == admin.id,
        )
    )
    m.role = "editor"
    await db.commit()
    monkeypatch.setattr(svc, "peek", AsyncMock(return_value=inv))
    with pytest.raises(svc.InvitationInvalid):
        await svc.redeem(db, invitee, code, now=NOW)
    assert await _role(db, ws, invitee) is None
