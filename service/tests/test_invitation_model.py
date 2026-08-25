from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.invitation import WorkspaceInvitation
from src.models.user import User
from src.models.workspace import Workspace
from tests.self_serve_fixtures import make_engine


@pytest_asyncio.fixture
async def db():
    engine = await make_engine()
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


async def _ws(db) -> Workspace:
    user = User(email="o@example.com", name="Owner")
    db.add(user)
    await db.flush()
    ws = Workspace(name="Acme", slug="acme", created_by=user.id)
    db.add(ws)
    await db.flush()
    return ws


@pytest.mark.asyncio
async def test_role_check_constraint(db):
    ws = await _ws(db)
    db.add(
        WorkspaceInvitation(
            workspace_id=ws.id,
            code_hash="h1",
            role="owner",
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()


@pytest.mark.asyncio
async def test_code_hash_unique(db):
    ws = await _ws(db)
    exp = datetime.now(UTC) + timedelta(days=7)
    db.add(
        WorkspaceInvitation(
            workspace_id=ws.id, code_hash="h", role="viewer", expires_at=exp
        )
    )
    await db.flush()
    db.add(
        WorkspaceInvitation(
            workspace_id=ws.id, code_hash="h", role="viewer", expires_at=exp
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()
