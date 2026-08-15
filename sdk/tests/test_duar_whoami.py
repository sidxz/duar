"""Duar.whoami: self-discovers effective_scope and routes clients under it."""

import httpx
import respx

from duar_auth import Duar


def _duar() -> Duar:
    return Duar(
        base_url="https://duar.test",
        service_name="docs",
        service_key="svc-key",
        idp_public_key="-----BEGIN PUBLIC KEY-----\nx\n-----END PUBLIC KEY-----",
        idp_audience="my-client-id",
    )


@respx.mock
async def test_member_resolves_realm_scope_and_rewires_clients():
    respx.get("https://duar.test/realm/whoami").mock(
        return_value=httpx.Response(
            200,
            json={
                "service_name": "docs",
                "effective_scope": "acme-suite",
                "realm": {"slug": "acme-suite", "name": "Acme Suite"},
            },
        )
    )
    s = _duar()
    # Touch the clients BEFORE whoami so they are created with the bare name —
    # whoami must then mutate them in place (the get_auth factory captures them).
    assert s.permissions.service_name == "docs"
    assert s.roles.service_name == "docs"

    data = await s.fetch_whoami()

    assert data["effective_scope"] == "acme-suite"
    assert s.effective_scope == "acme-suite"
    assert s.realm == {"slug": "acme-suite", "name": "Acme Suite"}
    assert s.permissions.service_name == "acme-suite"
    assert s.roles.service_name == "acme-suite"


@respx.mock
async def test_standalone_stays_on_service_name():
    respx.get("https://duar.test/realm/whoami").mock(
        return_value=httpx.Response(
            200,
            json={"service_name": "docs", "effective_scope": "docs", "realm": None},
        )
    )
    s = _duar()
    await s.fetch_whoami()
    assert s.effective_scope == "docs"
    assert s.realm is None
    assert s.permissions.service_name == "docs"


@respx.mock
async def test_pre_realm_duar_404_degrades_to_standalone():
    respx.get("https://duar.test/realm/whoami").mock(return_value=httpx.Response(404, json={"detail": "Not Found"}))
    s = _duar()
    data = await s.fetch_whoami()
    assert data is None
    assert s.effective_scope == "docs"  # falls back to service_name, no crash
    assert s.realm is None


@respx.mock
async def test_non_json_200_degrades_to_standalone():
    # An ingress misroute can serve the admin SPA (200 text/html) for /realm/whoami.
    # resp.json() raises ValueError — must degrade, not crash startup.
    respx.get("https://duar.test/realm/whoami").mock(
        return_value=httpx.Response(200, html="<!doctype html><html><title>Admin</title></html>")
    )
    s = _duar()
    data = await s.fetch_whoami()
    assert data is None
    assert s.effective_scope == "docs"
    assert s.realm is None
