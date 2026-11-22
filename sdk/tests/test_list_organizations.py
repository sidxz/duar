"""Duar.list_organizations: service-key call to the org directory, typed rows, clear errors."""

import uuid

import httpx
import pytest
import respx

from duar_auth import Duar, DuarError, OrgDirectoryEntry

ORG = {
    "id": str(uuid.uuid4()),
    "slug": "tamu",
    "name": "TAMU",
    "is_public": False,
    "enabled": False,
}


def _duar() -> Duar:
    return Duar(
        base_url="https://duar.test",
        service_name="docs",
        service_key="svc-key",
        idp_public_key="-----BEGIN PUBLIC KEY-----\nx\n-----END PUBLIC KEY-----",
        idp_audience="my-client-id",
    )


@respx.mock
async def test_returns_typed_entries_with_service_key_and_flag():
    route = respx.get("https://duar.test/organizations").mock(return_value=httpx.Response(200, json=[ORG]))

    orgs = await _duar().list_organizations(include_disabled=True)

    assert orgs == [
        OrgDirectoryEntry(id=uuid.UUID(ORG["id"]), slug="tamu", name="TAMU", is_public=False, enabled=False)
    ]
    req = route.calls.last.request
    assert req.headers["X-Service-Key"] == "svc-key"
    assert req.url.params["include_disabled"] == "true"


@respx.mock
async def test_default_omits_include_disabled():
    route = respx.get("https://duar.test/organizations").mock(return_value=httpx.Response(200, json=[]))
    assert await _duar().list_organizations() == []
    assert "include_disabled" not in route.calls.last.request.url.params


@respx.mock
async def test_rate_limited_raises_with_retry_after():
    respx.get("https://duar.test/organizations").mock(return_value=httpx.Response(429, headers={"Retry-After": "7"}))
    with pytest.raises(DuarError) as exc:
        await _duar().list_organizations()
    assert exc.value.status_code == 429
    assert exc.value.retry_after == "7"


@respx.mock
async def test_html_200_from_misrouted_ingress_raises():
    respx.get("https://duar.test/organizations").mock(
        return_value=httpx.Response(200, text="<!doctype html>", headers={"content-type": "text/html"})
    )
    with pytest.raises(DuarError, match="non-JSON"):
        await _duar().list_organizations()
