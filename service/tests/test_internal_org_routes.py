"""Behavioral tests for GET /organizations (internal, service-key-only directory).

Auth-boundary test drives the real require_service_key dependency (mirrors
test_authz_resolve_guard.py: no override, so the guard's own header check
fires). The data tests fake the service-key dependency (mirrors
test_realm_routes.py) and run against a real in-memory SQLite `organizations`
table (mirrors test_actions_insights.py) — the behavior under test is the
enabled-filter + ordering in list_orgs_for_directory, which a mocked service
call would not exercise.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.api.dependencies import ServiceKeyContext, require_service_key
from src.api.internal_org_routes import router as internal_org_router
from src.database import Base, get_db
from src.middleware.rate_limit import limiter, rate_limit_exceeded_handler
from src.models.organization import Organization

_SERVICE_KEY_HEADERS = {"X-Service-Key": "test-key"}


@pytest.fixture(autouse=True)
def _disable_limiter():
    original = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = original


@pytest_asyncio.fixture
async def db():
    """Real in-memory SQLite `organizations` table.

    SQLite can't express the model's partial unique index (WHERE is_public);
    compiled without the WHERE clause it becomes a full unique index on the
    column, which would reject having more than one non-public org. That
    constraint isn't under test here, so it's skipped for table creation only
    (same accommodation as test_actions_insights.py's JSONB shim).
    """
    engine = create_async_engine("sqlite+aiosqlite://")
    table = Organization.__table__
    partial_index = next(i for i in table.indexes if i.name == "uq_one_public_org")
    table.indexes.discard(partial_index)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[table]))
    finally:
        table.indexes.add(partial_index)
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


def _app(*, db=None, authed=False) -> FastAPI:
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(internal_org_router)
    if authed:
        app.dependency_overrides[require_service_key] = lambda: ServiceKeyContext(
            service_name="test-svc"
        )

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    return app


def _org(*, slug, name=None, is_public=False, enabled=True) -> Organization:
    return Organization(
        id=uuid.uuid4(),
        slug=slug,
        name=name or slug,
        is_public=is_public,
        enabled=enabled,
    )


def test_list_organizations_requires_service_key():
    client = TestClient(_app())
    resp = client.get("/organizations")
    assert resp.status_code == 401


async def _seed_orgs(db: AsyncSession) -> None:
    db.add_all(
        [
            _org(slug="public", name="Public", is_public=True, enabled=True),
            _org(slug="abbvie", name="AbbVie", enabled=True),
            _org(slug="oldco", name="OldCo", enabled=False),
        ]
    )
    await db.flush()


@pytest.mark.asyncio
async def test_list_organizations_returns_enabled_orgs(db):
    await _seed_orgs(db)
    client = TestClient(_app(db=db, authed=True))
    resp = client.get("/organizations", headers=_SERVICE_KEY_HEADERS)
    assert resp.status_code == 200
    slugs = {o["slug"] for o in resp.json()}
    assert "abbvie" in slugs and "oldco" not in slugs
    body = {o["slug"]: o for o in resp.json()}
    assert set(body["abbvie"]) == {"id", "slug", "name", "is_public", "enabled"}


@pytest.mark.asyncio
async def test_list_organizations_include_disabled(db):
    await _seed_orgs(db)
    client = TestClient(_app(db=db, authed=True))
    resp = client.get("/organizations?include_disabled=1", headers=_SERVICE_KEY_HEADERS)
    assert resp.status_code == 200
    assert "oldco" in {o["slug"] for o in resp.json()}


@pytest.mark.asyncio
async def test_list_organizations_orders_public_first_then_by_name(db):
    db.add_all(
        [
            _org(slug="zeta", name="Zeta Corp"),
            _org(slug="public", name="Public", is_public=True),
            _org(slug="acme", name="Acme Inc"),
        ]
    )
    await db.flush()
    client = TestClient(_app(db=db, authed=True))
    resp = client.get("/organizations", headers=_SERVICE_KEY_HEADERS)
    assert [o["slug"] for o in resp.json()] == ["public", "acme", "zeta"]
