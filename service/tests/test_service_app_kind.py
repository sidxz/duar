"""service_apps.kind — the additive MCP-server discriminator (Duar MCP).

Default is "service" (existing apps/behavior unchanged); "mcp_server" marks an
MCP server. FakeDB pattern (no real DB/Redis), symmetric with
test_service_app_idp_audiences.py.
"""

from __future__ import annotations

import pytest


class _FakeResult:
    def scalar_one_or_none(self):
        return None


class _FakeDB:
    async def execute(self, _stmt, *_args, **_kwargs):
        return _FakeResult()

    def add(self, _obj):
        pass

    async def flush(self):
        pass


def test_model_has_kind_column_defaulting_to_service():
    from src.models.service_app import ServiceApp

    col = ServiceApp.__table__.columns["kind"]
    assert col.nullable is False
    assert col.server_default.arg == "service"


@pytest.mark.asyncio
async def test_create_defaults_kind_to_service():
    from src.services import service_app_service

    app, _key = await service_app_service.create_service_app(
        _FakeDB(), name="Docs", service_name="docs"
    )
    assert app.kind == "service"


@pytest.mark.asyncio
async def test_create_can_mark_mcp_server():
    from src.services import service_app_service

    app, _key = await service_app_service.create_service_app(
        _FakeDB(), name="Chem MCP", service_name="chem-mcp", kind="mcp_server"
    )
    assert app.kind == "mcp_server"
