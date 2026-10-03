"""CORS preflight must allow every header the browser SDKs actually send.

The JS SDK's authz-mode client attaches X-Authz-Token to its member-directory
and share-dialog calls straight from the browser. If that header is missing
from allow_headers, the preflight 400s ("Disallowed CORS headers") and the
fetch throws before the request ever reaches the endpoint.
"""

import pytest
from fastapi.testclient import TestClient

import src.middleware.cors as cors
from src.main import create_app

ORIGIN = "https://app.example.com"

# Every custom header a browser client legitimately sends cross-origin.
BROWSER_SENT_HEADERS = [
    "Content-Type",
    "Authorization",
    "X-Requested-With",
    "X-Authz-Token",
]


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(cors, "_allowed_origins", {ORIGIN})
    return TestClient(create_app("all"))


def _preflight(client, headers: str):
    return client.options(
        "/workspaces/some-id/members",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": headers,
        },
    )


def test_preflight_allows_all_browser_sent_headers(client):
    res = _preflight(client, ", ".join(BROWSER_SENT_HEADERS))
    assert res.status_code == 200, res.text
    allowed = res.headers["access-control-allow-headers"].lower()
    for header in BROWSER_SENT_HEADERS:
        assert header.lower() in allowed


def test_preflight_rejects_unknown_header(client):
    res = _preflight(client, "X-Totally-Unknown")
    assert res.status_code == 400


def test_preflight_rejects_unknown_origin(client):
    res = client.options(
        "/workspaces/some-id/members",
        headers={
            "Origin": "https://evil.example.net",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Authz-Token",
        },
    )
    assert "access-control-allow-origin" not in res.headers
