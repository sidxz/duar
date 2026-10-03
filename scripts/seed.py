"""Seed the identity database with test data."""

import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import sys
from pathlib import Path

# Add service/ to sys.path so we can import src.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "service"))

from src.models.user import User, SocialAccount
from src.models.workspace import Workspace, WorkspaceMembership
from src.models.group import Group, GroupMembership
from src.models.permission import ResourcePermission, ResourceShare

DATABASE_URL = "postgresql+asyncpg://identity:identity_dev@localhost:9001/identity"


async def seed():
    engine = create_async_engine(DATABASE_URL)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as db:
        # Clear existing data (reverse FK order)
        for table in [ResourceShare, ResourcePermission, GroupMembership, Group,
                      WorkspaceMembership, Workspace, SocialAccount, User]:
            await db.execute(table.__table__.delete())
        await db.flush()

        # --- Users ---
        alice = User(id=uuid.uuid4(), email="alice@example.com", name="Alice Chen", avatar_url=None, is_active=True, is_admin=True)
        bob = User(id=uuid.uuid4(), email="bob@example.com", name="Bob Martinez", avatar_url=None, is_active=True)
        carol = User(id=uuid.uuid4(), email="carol@example.com", name="Carol Wang", avatar_url=None, is_active=True)
        dave = User(id=uuid.uuid4(), email="dave@example.com", name="Dave Johnson", avatar_url=None, is_active=False)
        eve = User(id=uuid.uuid4(), email="eve@gates.org", name="Eve Thompson", avatar_url=None, is_active=True)

        db.add_all([alice, bob, carol, dave, eve])
        await db.flush()

        # --- Social Accounts ---
        db.add_all([
            SocialAccount(id=uuid.uuid4(), user_id=alice.id, provider="google", provider_user_id="g-alice-001"),
            SocialAccount(id=uuid.uuid4(), user_id=bob.id, provider="github", provider_user_id="gh-bob-002"),
            SocialAccount(id=uuid.uuid4(), user_id=carol.id, provider="google", provider_user_id="g-carol-003"),
            SocialAccount(id=uuid.uuid4(), user_id=carol.id, provider="github", provider_user_id="gh-carol-003"),
            SocialAccount(id=uuid.uuid4(), user_id=eve.id, provider="entra", provider_user_id="entra-eve-005"),
        ])

        # --- Workspaces ---
        ws_acme = Workspace(id=uuid.uuid4(), slug="acme-corp", name="Acme Corporation", description="Main engineering workspace", created_by=alice.id)
        ws_research = Workspace(id=uuid.uuid4(), slug="research-lab", name="Research Lab", description="R&D projects", created_by=bob.id)
        ws_gates = Workspace(id=uuid.uuid4(), slug="gates-foundation", name="Gates Foundation", description="Foundation workspace", created_by=eve.id)

        db.add_all([ws_acme, ws_research, ws_gates])
        await db.flush()

        # --- Workspace Memberships ---
        db.add_all([
            # Acme Corp: alice=owner, bob=editor, carol=viewer
            WorkspaceMembership(id=uuid.uuid4(), workspace_id=ws_acme.id, user_id=alice.id, role="owner"),
            WorkspaceMembership(id=uuid.uuid4(), workspace_id=ws_acme.id, user_id=bob.id, role="editor"),
            WorkspaceMembership(id=uuid.uuid4(), workspace_id=ws_acme.id, user_id=carol.id, role="viewer"),
            # Research Lab: bob=owner, alice=admin, carol=editor
            WorkspaceMembership(id=uuid.uuid4(), workspace_id=ws_research.id, user_id=bob.id, role="owner"),
            WorkspaceMembership(id=uuid.uuid4(), workspace_id=ws_research.id, user_id=alice.id, role="admin"),
            WorkspaceMembership(id=uuid.uuid4(), workspace_id=ws_research.id, user_id=carol.id, role="editor"),
            # Gates Foundation: eve=owner, alice=viewer
            WorkspaceMembership(id=uuid.uuid4(), workspace_id=ws_gates.id, user_id=eve.id, role="owner"),
            WorkspaceMembership(id=uuid.uuid4(), workspace_id=ws_gates.id, user_id=alice.id, role="viewer"),
        ])

        # --- Groups ---
        grp_eng = Group(id=uuid.uuid4(), workspace_id=ws_acme.id, name="Engineering", description="Engineering team", created_by=alice.id)
        grp_design = Group(id=uuid.uuid4(), workspace_id=ws_acme.id, name="Design", description="Design team", created_by=alice.id)
        grp_ml = Group(id=uuid.uuid4(), workspace_id=ws_research.id, name="ML Team", description="Machine learning researchers", created_by=bob.id)

        db.add_all([grp_eng, grp_design, grp_ml])
        await db.flush()

        # --- Group Memberships ---
        db.add_all([
            GroupMembership(id=uuid.uuid4(), group_id=grp_eng.id, user_id=alice.id),
            GroupMembership(id=uuid.uuid4(), group_id=grp_eng.id, user_id=bob.id),
            GroupMembership(id=uuid.uuid4(), group_id=grp_design.id, user_id=carol.id),
            GroupMembership(id=uuid.uuid4(), group_id=grp_ml.id, user_id=bob.id),
            GroupMembership(id=uuid.uuid4(), group_id=grp_ml.id, user_id=carol.id),
        ])

        # --- Resource Permissions ---
        doc1 = ResourcePermission(
            id=uuid.uuid4(), service_name="docu-store", resource_type="document",
            resource_id=uuid.uuid4(), workspace_id=ws_acme.id, owner_id=alice.id, visibility="workspace",
        )
        doc2 = ResourcePermission(
            id=uuid.uuid4(), service_name="docu-store", resource_type="document",
            resource_id=uuid.uuid4(), workspace_id=ws_acme.id, owner_id=bob.id, visibility="private",
        )
        project1 = ResourcePermission(
            id=uuid.uuid4(), service_name="cage-fusion", resource_type="project",
            resource_id=uuid.uuid4(), workspace_id=ws_research.id, owner_id=bob.id, visibility="workspace",
        )

        db.add_all([doc1, doc2, project1])
        await db.flush()

        # --- Resource Shares ---
        db.add_all([
            ResourceShare(
                id=uuid.uuid4(), resource_permission_id=doc2.id,
                grantee_type="user", grantee_id=carol.id, permission="view", granted_by=bob.id,
            ),
            ResourceShare(
                id=uuid.uuid4(), resource_permission_id=doc2.id,
                grantee_type="group", grantee_id=grp_eng.id, permission="edit", granted_by=bob.id,
            ),
        ])

        await db.commit()

    await engine.dispose()
    print("Seeded: 5 users, 3 workspaces, 3 groups, 3 resources, 2 shares")


if __name__ == "__main__":
    asyncio.run(seed())
