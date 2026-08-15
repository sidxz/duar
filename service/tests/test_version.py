"""Regression guard for version drift.

The version surfaced by the running service (OpenAPI metadata + the admin
System Health tab) must track the installed ``duar`` package version,
never a hardcoded literal. Historically these were pinned to "0.1.0" while the
packages were released at 0.11.0, so the System tab showed a stale version.
"""

from __future__ import annotations

import uuid
from importlib.metadata import version as dist_version

from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded

from src.api import admin_routes
from src.api.dependencies import require_admin
from src.database import get_db
import pytest

from src.middleware.rate_limit import limiter, rate_limit_exceeded_handler


@pytest.fixture(autouse=True)
def _disable_limiter():
    """Disable the Redis-backed limiter for this module; restore after each test."""
    original = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = original


PACKAGE_VERSION = dist_version("duar-service")


def test_package_version_has_moved_past_the_old_placeholder():
    assert PACKAGE_VERSION != "0.1.0"


def test_version_module_tracks_package_metadata():
    from src.version import __version__

    assert __version__ == PACKAGE_VERSION


def test_fastapi_app_reports_package_version():
    from src.main import app

    assert app.version == PACKAGE_VERSION


def test_system_health_endpoint_reports_package_version(monkeypatch):
    class _FakeRedis:
        async def ping(self):
            return True

    async def _fake_get_redis():
        return _FakeRedis()

    monkeypatch.setattr(admin_routes.token_service, "get_redis", _fake_get_redis)

    class _FakeDB:
        async def execute(self, _stmt):
            return None

    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(admin_routes.router)
    app.dependency_overrides[require_admin] = lambda: {
        "sub": str(uuid.uuid4()),
        "admin": True,
    }

    async def _db():
        yield _FakeDB()

    app.dependency_overrides[get_db] = _db

    resp = TestClient(app).get("/admin/system/health")
    assert resp.status_code == 200
    assert resp.json()["version"] == PACKAGE_VERSION
