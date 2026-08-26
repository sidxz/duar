"""The hosted HTML pages self-host their fonts under /static (CSP font-src 'self')."""

from fastapi.testclient import TestClient

from src.config import settings
from src.main import create_app


def _client(tier: str) -> TestClient:
    # base_url sets the Host header so TrustedHostMiddleware lets the request through
    return TestClient(create_app(tier), base_url=settings.base_url)


def test_public_tier_serves_fonts():
    r = _client("public").get("/static/fonts/OverusedGrotesk-VF.woff2")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("font/woff2")


def test_static_does_not_escape_its_directory():
    r = _client("public").get("/static/../main.py")
    assert r.status_code == 404


def test_internal_tier_has_no_static_mount():
    r = _client("internal").get("/static/fonts/OverusedGrotesk-VF.woff2")
    assert r.status_code == 404
