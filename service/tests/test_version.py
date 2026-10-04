"""Regression guard for version drift.

The version surfaced by the running service (OpenAPI metadata + the admin
System Health tab) comes from ``APP_VERSION``, which CI sets from the release
tag. Historically it was a hardcoded "0.1.0", then package metadata that the
image never installs, so every image showed "0.0.0+unknown".
"""

from __future__ import annotations

import importlib
import uuid

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


def test_version_comes_from_app_version_env(monkeypatch):
    import src.version as version_mod

    monkeypatch.setenv("APP_VERSION", "9.9.9")
    assert importlib.reload(version_mod).__version__ == "9.9.9"
    monkeypatch.delenv("APP_VERSION")
    assert importlib.reload(version_mod).__version__ == "0.0.0+dev"


def test_fastapi_app_reports_version():
    from src.main import app
    from src.version import __version__

    assert app.version == __version__


def test_system_health_endpoint_reports_version(monkeypatch):
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
    assert resp.json()["version"] == admin_routes.__version__
