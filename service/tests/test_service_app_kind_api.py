"""Surface service_apps.kind through the admin API (Duar MCP console, Task C1).

The additive `kind` column already exists (core branch); these pin the admin
surface the console needs: the response exposes it, create accepts/forwards it,
and the list route filters by it so an MCP server never shows in the plain
Services list and vice-versa (Review Focus 1, 2).

Filter correctness is proven at the service layer by compiling the real
statement (a real SQLite round-trip is infeasible — service_apps has ARRAY
columns that can't bind lists on SQLite; see ledger ruling). Route wiring uses
the house minimal-app + dependency-override pattern (test_org_admin_routes.py).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from slowapi.errors import RateLimitExceeded

from src.api.admin_routes import router as admin_router
from src.api.dependencies import require_admin
from src.database import get_db
from src.middleware.rate_limit import limiter, rate_limit_exceeded_handler
from src.schemas.service_app import ServiceAppCreateRequest, ServiceAppResponse

_XRW = {"X-Requested-With": "XMLHttpRequest"}


# ---------------------------------------------------------------------------
# Schemas — the console reads/sets kind
# ---------------------------------------------------------------------------


def test_response_exposes_kind_from_attributes():
    class _Obj:
        id = uuid.uuid4()
        name = "Chem MCP"
        service_name = "chem-mcp"
        realm_id = None
        key_prefix = "sk_aaaa"
        is_active = True
        allowed_origins: list[str] = []
        allowed_idp_audiences: list[str] = []
        last_used_at = None
        created_by = None
        created_at = datetime.now(UTC)
        updated_at = datetime.now(UTC)
        kind = "mcp_server"

    resp = ServiceAppResponse.model_validate(_Obj())
    assert resp.kind == "mcp_server"


def test_response_kind_defaults_to_service():
    class _Obj:
        id = uuid.uuid4()
        name = "Docs"
        service_name = "docs"
        realm_id = None
        key_prefix = "sk_bbbb"
        is_active = True
        allowed_origins: list[str] = []
        allowed_idp_audiences: list[str] = []
        last_used_at = None
        created_by = None
        created_at = datetime.now(UTC)
        updated_at = datetime.now(UTC)
        # no kind attribute -> schema default

    resp = ServiceAppResponse.model_validate(_Obj())
    assert resp.kind == "service"


def test_create_request_accepts_kind_and_defaults_to_service():
    assert ServiceAppCreateRequest(name="Docs", service_name="docs").kind == "service"
    assert (
        ServiceAppCreateRequest(
            name="Chem MCP", service_name="chem-mcp", kind="mcp_server"
        ).kind
        == "mcp_server"
    )


def test_create_request_rejects_unknown_kind():
    """Only the two known kinds are valid — an arbitrary value would persist and
    then match neither list filter, making the app unmanageable from both consoles."""
    with pytest.raises(ValidationError):
        ServiceAppCreateRequest(name="X", service_name="x-svc", kind="banana")


# ---------------------------------------------------------------------------
# Service layer — the filter actually constrains the query (Review Focus 1)
# ---------------------------------------------------------------------------


class _CapturingDB:
    """Records every statement passed to execute; returns an empty scalar set."""

    def __init__(self):
        self.statements: list = []

    async def execute(self, stmt, *_a, **_k):
        self.statements.append(stmt)
        return _EmptyScalars()


class _EmptyScalars:
    def scalars(self):
        return self

    def all(self):
        return []


def _sql(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


@pytest.mark.asyncio
async def test_list_filters_by_kind_when_given():
    from src.services import service_app_service

    db = _CapturingDB()
    await service_app_service.list_service_apps(db, kind="mcp_server")
    sql = _sql(db.statements[0])
    assert "WHERE service_apps.kind = 'mcp_server'" in sql


@pytest.mark.asyncio
async def test_list_has_no_kind_filter_when_omitted():
    from src.services import service_app_service

    db = _CapturingDB()
    await service_app_service.list_service_apps(db)
    sql = _sql(db.statements[0])
    assert "WHERE" not in sql


# ---------------------------------------------------------------------------
# Route wiring — GET forwards ?kind=; POST forwards body.kind and echoes it
# ---------------------------------------------------------------------------


class _FakeApp:
    def __init__(self, kind="service"):
        self.id = uuid.uuid4()
        self.name = "Chem MCP"
        self.service_name = "chem-mcp"
        self.realm_id = None
        self.key_prefix = "sk_aaaa"
        self.is_active = True
        self.allowed_origins: list[str] = []
        self.allowed_idp_audiences: list[str] = []
        self.last_used_at = None
        self.created_by = None
        self.created_at = datetime.now(UTC)
        self.updated_at = datetime.now(UTC)
        self.kind = kind


class _FakeDB:
    async def commit(self):
        pass

    async def refresh(self, _obj):
        pass


@pytest.fixture(autouse=True)
def _disable_limiter():
    original = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = original


def _client(db) -> TestClient:
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(admin_router)
    app.dependency_overrides[require_admin] = lambda: {
        "sub": str(uuid.uuid4()),
        "admin": True,
    }

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def test_list_route_forwards_kind_query(monkeypatch):
    from src.services import service_app_service

    seen = {}

    async def _fake_list(_db, kind=None):
        seen["kind"] = kind
        return []

    monkeypatch.setattr(service_app_service, "list_service_apps", _fake_list)

    client = _client(_FakeDB())
    assert (
        client.get("/admin/service-apps?kind=mcp_server", headers=_XRW).status_code
        == 200
    )
    assert seen["kind"] == "mcp_server"

    client.get("/admin/service-apps", headers=_XRW)
    assert seen["kind"] is None


def test_create_route_forwards_kind_and_echoes_it(monkeypatch):
    import src.api.admin_routes as admin_routes
    from src.services import activity_service, service_app_service

    seen = {}

    async def _fake_create(_db, **kwargs):
        seen.update(kwargs)
        return _FakeApp(kind=kwargs.get("kind", "service")), "sk_plaintext_key"

    async def _noop(*_a, **_k):
        pass

    monkeypatch.setattr(service_app_service, "create_service_app", _fake_create)
    monkeypatch.setattr(service_app_service, "invalidate_cache", _noop)
    monkeypatch.setattr(activity_service, "log_activity", _noop)
    monkeypatch.setattr(admin_routes, "refresh_origins", _noop)

    client = _client(_FakeDB())
    r = client.post(
        "/admin/service-apps",
        json={"name": "Chem MCP", "service_name": "chem-mcp", "kind": "mcp_server"},
        headers=_XRW,
    )
    assert r.status_code == 201, r.text
    assert seen["kind"] == "mcp_server"
    assert r.json()["kind"] == "mcp_server"
