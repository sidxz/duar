"""create_self_serve: flag, per-user cap, hourly breaker, generated slug."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models.user import User
from src.models.workspace import Workspace, WorkspaceMembership
from src.services import workspace_service
from src.services.workspace_service import (
    SelfServeCapReached,
    SelfServeDisabled,
    SelfServeThrottled,
    create_self_serve,
    slugify,
)
from tests.self_serve_fixtures import make_engine

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def db():
    engine = await make_engine()
    # expire_on_commit=False: create_self_serve/create_workspace commit and
    # return the row; matches src/database.py's real session factory.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    monkeypatch.setattr(settings, "self_serve_enabled", True)
    monkeypatch.setattr(settings, "self_serve_max_workspaces_per_user", 1)
    monkeypatch.setattr(settings, "self_serve_max_creates_per_hour", 30)


async def _user(db, email="u@example.com") -> User:
    user = User(email=email, name="U")
    db.add(user)
    await db.flush()
    return user


def test_slugify():
    assert slugify("Acme Corp!") == "acme-corp"
    assert slugify("  --Hello__World-- ") == "hello-world"
    assert slugify("!!!") == "ws"
    assert len(slugify("x" * 100)) == 40


@pytest.mark.asyncio
async def test_creates_workspace_with_generated_slug_and_owner(db):
    user = await _user(db)
    ws = await create_self_serve(db, user, "Acme Corp", now=NOW)
    assert ws.slug.startswith("acme-corp-") and len(ws.slug) == len("acme-corp-") + 4
    assert ws.created_by == user.id
    role = await workspace_service.get_member_role(db, ws.id, user.id)
    assert role == "owner"


@pytest.mark.asyncio
async def test_slug_collision_retry_does_not_expire_user(db, monkeypatch):
    """Regression: db.rollback() in the retry loop used to expire `user`, and
    the next iteration's `user.id` lazy-load crashed with MissingGreenlet."""
    other = await _user(db, "other@example.com")
    db.add(Workspace(name="Acme", slug="acme-aaaa", created_by=other.id))
    await db.commit()
    monkeypatch.setattr(
        workspace_service.secrets, "token_hex", Mock(side_effect=["aaaa", "bbbb"])
    )
    user = await _user(db)
    ws = await create_self_serve(db, user, "Acme", now=NOW)
    assert ws.slug == "acme-bbbb"
    assert await workspace_service.get_member_role(db, ws.id, user.id) == "owner"


@pytest.mark.asyncio
async def test_flag_off_raises(db, monkeypatch):
    monkeypatch.setattr(settings, "self_serve_enabled", False)
    user = await _user(db)
    with pytest.raises(SelfServeDisabled):
        await create_self_serve(db, user, "Acme", now=NOW)


@pytest.mark.asyncio
async def test_cap_counts_created_by(db):
    user = await _user(db)
    await create_self_serve(db, user, "One", now=NOW)
    with pytest.raises(SelfServeCapReached):
        await create_self_serve(db, user, "Two", now=NOW)
    assert await workspace_service.count_created_by(db, user.id) == 1


@pytest.mark.asyncio
async def test_cap_zero_is_join_only(db, monkeypatch):
    monkeypatch.setattr(settings, "self_serve_max_workspaces_per_user", 0)
    user = await _user(db)
    with pytest.raises(SelfServeCapReached):
        await create_self_serve(db, user, "One", now=NOW)


@pytest.mark.asyncio
async def test_breaker_counts_recent_workspaces_instance_wide(db, monkeypatch):
    monkeypatch.setattr(settings, "self_serve_max_creates_per_hour", 2)
    other = await _user(db, "o@example.com")
    for i in range(2):
        db.add(
            Workspace(
                name=f"w{i}",
                slug=f"w{i}",
                created_by=other.id,
                created_at=NOW - timedelta(minutes=10),
            )
        )
    await db.flush()
    user = await _user(db)
    with pytest.raises(SelfServeThrottled):
        await create_self_serve(db, user, "Mine", now=NOW)


@pytest.mark.asyncio
async def test_breaker_ignores_old_workspaces(db, monkeypatch):
    monkeypatch.setattr(settings, "self_serve_max_creates_per_hour", 1)
    other = await _user(db, "o@example.com")
    db.add(
        Workspace(
            name="old",
            slug="old",
            created_by=other.id,
            created_at=NOW - timedelta(hours=2),
        )
    )
    await db.flush()
    user = await _user(db)
    ws = await create_self_serve(db, user, "Mine", now=NOW)
    assert ws.id


@pytest.mark.asyncio
async def test_caller_slug_is_used_verbatim(db):
    user = await _user(db)
    ws = await create_self_serve(db, user, "Mine", slug="my-slug", now=NOW)
    assert ws.slug == "my-slug"


@pytest.mark.asyncio
async def test_caller_slug_collision_is_value_error(db):
    a = await _user(db, "a@example.com")
    b = await _user(db, "b@example.com")
    await create_self_serve(db, a, "A", slug="taken", now=NOW)
    with pytest.raises(ValueError):
        await create_self_serve(db, b, "B", slug="taken", now=NOW)


@pytest.mark.asyncio
async def test_list_admin_workspaces_filters_role(db):
    user = await _user(db)
    ws = await create_self_serve(db, user, "Mine", now=NOW)  # owner
    viewer_ws = Workspace(name="v", slug="v", created_by=None)
    db.add(viewer_ws)
    await db.flush()
    db.add(
        WorkspaceMembership(workspace_id=viewer_ws.id, user_id=user.id, role="viewer")
    )
    await db.commit()
    rows = await workspace_service.list_admin_workspaces(db, user.id)
    assert [(w.id, r) for w, r in rows] == [(ws.id, "owner")]
