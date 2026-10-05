"""lookup_accessible_resources against in-memory SQLite.

Regression: the non-privileged path read ``.c`` straight off ``union(...)``,
which SQLAlchemy 2.1 removed, so every non-admin ``/permissions/accessible``
call 500'd in the published image (it ships 2.1; the lockfile pins 2.0).
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.database import Base
from src.models.permission import ResourcePermission, ResourceShare
from src.services.permission_service import lookup_accessible_resources

SVC, TYPE = "realm-x", "studio_protocol"


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://")
    tables = ["users", "workspaces", "resource_permissions", "resource_shares"]
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c, tables=[Base.metadata.tables[n] for n in tables]
            )
        )
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_member_sees_owned_workspace_visible_and_shared_only(db):
    ws, me, other, group = (uuid.uuid4() for _ in range(4))

    def perm(owner, visibility):
        p = ResourcePermission(
            service_name=SVC,
            resource_type=TYPE,
            resource_id=uuid.uuid4(),
            workspace_id=ws,
            owner_id=owner,
            visibility=visibility,
        )
        db.add(p)
        return p

    mine = perm(me, "private")
    public = perm(other, "workspace")
    hidden = perm(other, "private")
    shared_to_me = perm(other, "private")
    shared_to_group = perm(other, "private")
    await db.flush()
    db.add_all(
        [
            ResourceShare(
                resource_permission_id=shared_to_me.id,
                grantee_type="user",
                grantee_id=me,
                permission="view",
            ),
            ResourceShare(
                resource_permission_id=shared_to_group.id,
                grantee_type="group",
                grantee_id=group,
                permission="view",
            ),
        ]
    )
    await db.commit()

    ids, full = await lookup_accessible_resources(
        db,
        user_id=me,
        workspace_id=ws,
        workspace_role="viewer",
        group_ids=[group],
        service_name=SVC,
        resource_type=TYPE,
        action="view",
    )

    assert full is False
    assert set(ids) == {
        mine.resource_id,
        public.resource_id,
        shared_to_me.resource_id,
        shared_to_group.resource_id,
    }
    assert hidden.resource_id not in ids
