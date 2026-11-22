"""list_members returns each member's org_id (the stored stamp, same as the oid claim)."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.organization import Organization
from src.models.user import User
from src.models.workspace import Workspace, WorkspaceMembership
from src.schemas.workspace import WorkspaceMemberResponse
from src.services import workspace_service
from tests.self_serve_fixtures import make_engine


@pytest_asyncio.fixture
async def db():
    engine = await make_engine()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_list_members_includes_org_id(db):
    org = Organization(
        id=uuid.uuid4(), slug="tamu", name="TAMU", is_public=False, enabled=True
    )
    with_org = User(
        email="a@tamu.edu", name="a", organization_id=org.id, is_active=False
    )
    no_org = User(email="b@example.com", name="b")
    db.add_all([org, with_org, no_org])
    await db.flush()
    ws = Workspace(name="W", slug="w", created_by=with_org.id)
    db.add(ws)
    await db.flush()
    db.add_all(
        [
            WorkspaceMembership(workspace_id=ws.id, user_id=with_org.id, role="owner"),
            WorkspaceMembership(workspace_id=ws.id, user_id=no_org.id, role="viewer"),
        ]
    )
    await db.commit()

    rows = [
        WorkspaceMemberResponse(**r)
        for r in await workspace_service.list_members(db, ws.id)
    ]

    # Deactivated users keep their membership, so they still resolve.
    assert {r.user_id: r.org_id for r in rows} == {with_org.id: org.id, no_org.id: None}
