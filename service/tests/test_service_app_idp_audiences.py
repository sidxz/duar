"""Admin config surface for per-app IdP audience binding (allowed_idp_audiences).

The enforcement lives in /authz/resolve (see test_authz_resolve_audience_binding);
these cover the operator-facing path to actually configure it: request validation
(sanitize / reject empty) and persistence through create/update, symmetric to
allowed_origins.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from src.schemas.service_app import ServiceAppCreateRequest, ServiceAppUpdateRequest


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------


def test_create_request_sanitizes_audiences():
    """Audiences are HTML/whitespace-stripped, order preserved."""
    req = ServiceAppCreateRequest(
        name="App A",
        service_name="app-a",
        allowed_idp_audiences=["  client-a.apps.example.com  ", "<b>client-b</b>"],
    )
    assert req.allowed_idp_audiences == ["client-a.apps.example.com", "client-b"]


def test_create_request_rejects_empty_audience():
    """A blank/whitespace-only audience would never match a real aud — reject it
    rather than silently storing a dead entry."""
    with pytest.raises(ValidationError):
        ServiceAppCreateRequest(
            name="App A", service_name="app-a", allowed_idp_audiences=["client-a", "  "]
        )


def test_create_request_defaults_to_empty():
    """Unset => empty list => fall back to the deployment-wide audience (prior behavior)."""
    req = ServiceAppCreateRequest(name="App A", service_name="app-a")
    assert req.allowed_idp_audiences == []


def test_update_request_none_means_unchanged_empty_means_clear():
    """None leaves the field untouched; [] explicitly clears (disables binding)."""
    assert ServiceAppUpdateRequest().allowed_idp_audiences is None
    assert ServiceAppUpdateRequest(allowed_idp_audiences=[]).allowed_idp_audiences == []
    assert ServiceAppUpdateRequest(
        allowed_idp_audiences=[" client-a "]
    ).allowed_idp_audiences == ["client-a"]


# ---------------------------------------------------------------------------
# Persistence (service layer) — FakeDB + no-op cache (no real DB/Redis)
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, value=None):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDB:
    def __init__(self, get_result=None, execute_result=None):
        self._get = get_result
        self._execute = execute_result

    async def get(self, _model, _pk):
        return self._get

    async def execute(self, _stmt, *_args, **_kwargs):
        return _FakeResult(self._execute)

    def add(self, _obj):
        pass

    async def flush(self):
        pass


@pytest.mark.asyncio
async def test_create_service_app_persists_audiences(monkeypatch):
    from src.services import service_app_service

    app, _key = await service_app_service.create_service_app(
        _FakeDB(),
        name="App A",
        service_name="app-a",
        allowed_idp_audiences=["client-a"],
    )
    assert app.allowed_idp_audiences == ["client-a"]


@pytest.mark.asyncio
async def test_update_service_app_sets_and_preserves_audiences(monkeypatch):
    from src.models.service_app import ServiceApp
    from src.services import service_app_service

    existing = ServiceApp(
        id=uuid.uuid4(),
        name="App A",
        service_name="app-a",
        key_hash="x" * 64,
        key_prefix="sk_xxxxxxxx",
        allowed_origins=[],
        allowed_idp_audiences=["old-client"],
    )

    updated = await service_app_service.update_service_app(
        _FakeDB(get_result=existing), existing.id, allowed_idp_audiences=["new-client"]
    )
    assert updated.allowed_idp_audiences == ["new-client"]

    # None => unchanged
    again = await service_app_service.update_service_app(
        _FakeDB(get_result=updated), existing.id, name="Renamed"
    )
    assert again.allowed_idp_audiences == ["new-client"]
