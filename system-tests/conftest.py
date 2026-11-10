"""Shared pieces of the system tests.

Everything here talks to the running stack over HTTP, the way a browser, an
operator or a backend would. Nothing imports the service or reads its database:
the world the tests share (workspace, apps, members) is built through the admin
API after a real sign-in, starting from the empty database the stack booted on.
"""

import base64
import hashlib
import html
import json
import os
import re
import secrets
import ssl
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
import pytest

WORK = Path(os.environ.get("SYSTEM_TEST_DIR", Path(__file__).parent / ".work"))
TLS = ssl.create_default_context(cafile=str(WORK / "tls" / "ca.crt"))  # trust the stack's CA

BASE = "https://localhost:19443"  # the service, through the TLS proxy
ADMIN = "https://localhost:19444"  # the admin panel, through the TLS proxy
INTERNAL = "http://localhost:19010"  # the internal listener (unpublished in production)

# A client app's registered redirect URI. Nothing listens there: sign-in ends
# when the browser is sent to it, and the test reads the code off that URL.
APP_CALLBACK = "https://app.duar.test/callback"

# AuthZ mode: the issuer and audience the stack trusts (docker-compose.system-test.yml).
IDP_ISSUER = "https://idp.system-test.invalid"
IDP_AUDIENCE = "system-test-client"
IDP_PRIVATE_KEY = (WORK / "idp-private.pem").read_text()
IDP_PUBLIC_KEY = (WORK / "idp-public.pem").read_text()

REDIRECTS = (301, 302, 303, 307, 308)


class Browser:
    """One browser profile: a cookie jar, and redirects followed one hop at a time."""

    def __init__(self):
        self.http = httpx.Client(verify=TLS, timeout=20)

    def follow(self, url, *, until=None, method="GET", data=None):
        """Follow redirects starting at ``url``.

        Returns the first response that is not a redirect, or, when a redirect
        points at ``until``, that URL without fetching it (the browser leaving
        for the client app).
        """
        for _ in range(12):
            # Dex's issuer is its name on the compose network; from the host
            # the same listener is a published port.
            url = url.replace("http://dex:5556", "http://localhost:15556")
            response = self.http.request(method, url, data=data)
            if response.status_code not in REDIRECTS:
                return response
            url = str(response.url.join(response.headers["location"]))
            if until and url.startswith(until):
                return url
            method, data = "GET", None
        raise AssertionError(f"still redirecting after 12 hops, last to {url}")

    def sign_in(self, start_url, email, *, until, password="password"):
        """Start at a Duar login URL, sign in at Dex as ``email``, come back."""
        form = self.follow(start_url)
        assert form.status_code == 200 and 'name="password"' in form.text, (
            f"expected the Dex sign-in form, got {form.status_code} at {form.url}: {form.text[:300]}"
        )
        action = re.search(r'<form method="post" action="([^"]+)"', form.text).group(1)
        return self.follow(
            str(form.url.join(html.unescape(action))),
            until=until,
            method="POST",
            data={"login": email, "password": password},
        )


class AdminApi:
    """The admin API as the admin panel calls it: through the panel's own
    ``/api`` proxy, with the session cookie and the CSRF header."""

    def __init__(self, browser):
        self.browser = browser

    def request(self, method, path, **kwargs):
        headers = {"X-Requested-With": "XMLHttpRequest", **kwargs.pop("headers", {})}
        return self.browser.http.request(method, f"{ADMIN}/api{path}", headers=headers, **kwargs)

    def call(self, method, path, expect, **kwargs):
        response = self.request(method, path, **kwargs)
        assert response.status_code == expect, f"{method} {path}: {response.status_code} {response.text[:300]}"
        return response.json() if response.content else None

    def get(self, path, **kwargs):
        return self.call("GET", path, 200, **kwargs)

    def post(self, path, body=None, expect=201):
        return self.call("POST", path, expect, json=body)


def pkce():
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def login_url(client_id, challenge, *, redirect_uri=APP_CALLBACK, state="s1"):
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
    )
    return f"{BASE}/auth/login/dex?{query}"


def authorization_code(world, email, *, state="s1"):
    """Sign ``email`` in through Dex. Returns (browser, code, verifier, returned state)."""
    browser = Browser()
    verifier, challenge = pkce()
    landed = browser.sign_in(login_url(world.client_app_id, challenge, state=state), email, until=APP_CALLBACK)
    assert isinstance(landed, str), f"sign-in did not return to the app: {landed.status_code} {landed.text[:300]}"
    query = parse_qs(urlsplit(landed).query)
    return browser, query["code"][0], verifier, query.get("state", [None])[0]


def user_session(world, email):
    """A full proxy-mode sign-in: code, workspace choice, tokens."""
    browser, code, verifier, _ = authorization_code(world, email)
    tokens = browser.http.post(
        f"{BASE}/auth/token",
        json={"code": code, "workspace_id": world.workspace_id, "code_verifier": verifier},
    )
    assert tokens.status_code == 200, tokens.text
    return SimpleNamespace(browser=browser, **tokens.json())


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def idp_token(email, *, audience=IDP_AUDIENCE, issuer=IDP_ISSUER, key=IDP_PRIVATE_KEY, lifetime=300, **claims):
    """An ID token as the AuthZ-mode identity provider would issue it."""
    now = int(time.time())
    payload = {
        "iss": issuer,
        "aud": audience,
        "sub": f"idp|{email}",
        "email": email,
        "email_verified": True,
        "name": email.split("@")[0].title(),
        "iat": now,
        "exp": now + lifetime,
        **claims,
    }
    return jwt.encode(payload, key, algorithm="RS256")


def service_headers(key, token=None):
    headers = {"X-Service-Key": key}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


@pytest.fixture(scope="session")
def api():
    """A plain HTTP client for the service: no cookies, the stack's CA."""
    with httpx.Client(verify=TLS, timeout=20) as client:
        yield client


@pytest.fixture(scope="session")
def admin():
    """The first operator: signs in through Dex and is an admin because the
    address is in ADMIN_EMAILS. Nothing exists in the database before this."""
    browser = Browser()
    landed = browser.sign_in(f"{BASE}/auth/admin/login/dex", "admin@duar.test", until=ADMIN)
    assert landed == f"{ADMIN}/", f"admin sign-in ended at {landed!r}"
    return AdminApi(browser)


@pytest.fixture(scope="session")
def world(admin):
    """What an operator sets up before any app can use Duar, done through the
    admin API: a workspace, a login app, service apps, and invited members."""
    # Names carry a per-run suffix so the suite can be re-run against a stack
    # left up with KEEP=1.
    run = secrets.token_hex(3)
    workspace = admin.post("/admin/workspaces", {"name": "Acme", "slug": f"acme-{run}"})
    elsewhere = admin.post("/admin/workspaces", {"name": "Elsewhere", "slug": f"elsewhere-{run}"})
    client_app = admin.post("/admin/client-apps", {"name": "Acme web", "redirect_uris": [APP_CALLBACK]})

    def service_app(name):
        return admin.post(
            "/admin/service-apps",
            {"name": name, "service_name": f"{name}-{run}", "allowed_idp_audiences": [IDP_AUDIENCE]},
        )

    notes = service_app("notes")
    # Two apps that trust each other: members of one realm.
    billing, ledger = service_app("billing"), service_app("ledger")
    realm = admin.post("/admin/realms", {"name": "Finance", "slug": f"finance-{run}"})
    for app in (billing, ledger):
        admin.post(f"/admin/realms/{realm['id']}/members/{app['id']}")

    # Members are pre-provisioned by CSV import, before any of them has signed
    # in. alice, bob and eve then sign in through Duar (proxy mode); carol and
    # dave through their app's own identity provider (AuthZ mode). frank belongs
    # to the other workspace only.
    roles = {"alice": "admin", "bob": "viewer", "eve": "viewer", "carol": "editor", "dave": "viewer"}
    rows = [f"{name}@duar.test,{name.title()},{workspace['slug']},{role}" for name, role in roles.items()]
    rows.append(f"frank@duar.test,Frank,{elsewhere['slug']},editor")
    imported = admin.call(
        "POST",
        "/admin/import/csv/execute",
        200,
        files={"file": ("members.csv", "\n".join(["email,name,workspace_slug,role", *rows]), "text/csv")},
    )
    assert imported["memberships_added"] == len(rows) and imported["errors"] == [], imported
    users = {u["email"]: u["id"] for u in admin.get("/admin/users", params={"page_size": 100})["items"]}

    made = SimpleNamespace(
        workspace_id=workspace["id"],
        other_workspace_id=elsewhere["id"],
        client_app_id=client_app["id"],
        notes=notes["service_name"],
        notes_key=notes["api_key"],
        billing_key=billing["api_key"],
        ledger_key=ledger["api_key"],
        billing=billing["service_name"],
        ledger=ledger["service_name"],
        realm=realm["slug"],
        users=users,
    )
    # The JS tests run after these and start from the same world.
    (WORK / "world.json").write_text(
        json.dumps(
            {
                "base": BASE,
                "appCallback": APP_CALLBACK,
                "idpIssuer": IDP_ISSUER,
                "idpAudience": IDP_AUDIENCE,
                **vars(made),
            }
        )
    )
    return made


@pytest.fixture(scope="session")
def alice(world):
    return user_session(world, "alice@duar.test")


@pytest.fixture(scope="session")
def bob(world):
    return user_session(world, "bob@duar.test")
