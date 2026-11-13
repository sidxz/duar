"""A still-valid token can outlive the row it names: the user was deleted, or
the workspace was already removed by an earlier request. Writes against the
missing row answer 404.

Runs the real route functions and services against in-memory SQLite.
"""

import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.api import user_routes, workspace_routes
from src.api.dependencies import CurrentUser
from src.schemas.user import UserUpdateRequest
from tests.self_serve_fixtures import make_engine


@pytest_asyncio.fixture
async def db():
    engine = await make_engine()
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


def _owner_of(workspace_id):
    return CurrentUser(
        user_id=uuid.uuid4(),
        workspace_id=workspace_id,
        workspace_role="owner",
        groups=[],
    )


@pytest.mark.asyncio
async def test_updating_a_profile_whose_user_is_gone_is_404(db):
    with pytest.raises(HTTPException) as raised:
        await user_routes.update_me(
            UserUpdateRequest(name="New Name"), _owner_of(uuid.uuid4()), db
        )
    assert raised.value.status_code == 404


@pytest.mark.asyncio
async def test_deleting_a_workspace_that_is_gone_is_404(db):
    workspace_id = uuid.uuid4()
    with pytest.raises(HTTPException) as raised:
        await workspace_routes.delete_workspace(
            workspace_id, _owner_of(workspace_id), db
        )
    assert raised.value.status_code == 404
