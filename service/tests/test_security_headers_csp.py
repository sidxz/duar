"""Regression guard for Content-Security-Policy completeness.

`default-src 'none'` does NOT cover the *no-fallback* CSP directives (`form-action`,
`base-uri`) — they must be set explicitly or they are simply unconstrained. A live ZAP
active scan flagged this (alert 10055, "Failure to Define Directive with No Fallback").

The API CSP pins both to `'none'`; the HTML override pins `base-uri 'none'` and `form-action 'self'`
(hosted forms). The HTML override still allows `style-src 'unsafe-inline'` for the rendered
HTML — a deliberate, documented trade-off that is out of scope for this guard.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from src.middleware.security_headers import SecurityHeadersMiddleware


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/plain")
    def plain():
        return {"ok": True}

    @app.get("/html")
    def html():
        # Mirror how auth_routes.py opts an HTML page into the looser CSP.
        resp = PlainTextResponse("<html></html>")
        resp.headers["X-CSP-Override"] = "html-page"
        return resp

    @app.get("/onboard/home")
    def onboard():
        resp = PlainTextResponse("<html></html>")
        resp.headers["X-CSP-Override"] = "html-page"
        return resp

    return app


def _csp(path: str) -> str:
    resp = TestClient(_app()).get(path)
    return resp.headers["Content-Security-Policy"]


def test_default_csp_defines_form_action_and_base_uri():
    csp = _csp("/plain")
    assert "form-action 'none'" in csp
    assert "base-uri 'none'" in csp


def test_default_csp_retains_existing_directives():
    csp = _csp("/plain")
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_html_override_csp_allows_same_origin_forms_only():
    # Hosted onboarding pages POST plain forms to Duar itself; nowhere else.
    csp = _csp("/html")
    assert "form-action 'self'" in csp
    assert "form-action 'none'" not in csp
    assert "base-uri 'none'" in csp


def test_html_override_still_allows_inline_styles_for_login_page():
    # Documented, deliberate trade-off — the login page renders inline styles + an SVG.
    csp = _csp("/html")
    assert "style-src 'unsafe-inline'" in csp


def test_onboard_pages_are_no_store():
    resp = TestClient(_app()).get("/onboard/home")
    assert resp.headers["Cache-Control"] == "no-store"


def test_plain_api_route_is_not_no_store():
    resp = TestClient(_app()).get("/plain")
    assert resp.headers.get("Cache-Control") != "no-store"
