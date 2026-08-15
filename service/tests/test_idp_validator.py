"""Tests for the IdP token validator service."""

from __future__ import annotations

import time

import httpx
import jwt as pyjwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

from src.config import settings
from src.services.idp_validator import IdpValidationError, validate_idp_token


# ---------------------------------------------------------------------------
# RSA keypair fixture (module-scoped — expensive to generate)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rsa_keypair():
    """Generate an RSA keypair for signing test JWTs."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    return private_key, public_key


# ---------------------------------------------------------------------------
# Token helper fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def make_token(rsa_keypair):
    """Factory fixture that creates signed JWTs with given claims."""
    private_key, _ = rsa_keypair

    def _make(claims: dict, *, exp_offset: int = 3600) -> str:
        now = int(time.time())
        payload = {
            "iat": now,
            "exp": now + exp_offset,
            **claims,
        }
        pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return pyjwt.encode(payload, pem, algorithm="RS256")

    return _make


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_google_token(rsa_keypair, make_token):
    """A valid Google-style OIDC token returns normalised claims."""
    _, public_key = rsa_keypair

    token = make_token(
        {
            "sub": "google-user-123",
            "email": "user@example.com",
            "name": "Test User",
            "email_verified": True,
            "picture": "https://example.com/photo.jpg",
        }
    )

    result = await validate_idp_token(token, "google", _override_key=public_key)

    assert result["sub"] == "google-user-123"
    assert result["email"] == "user@example.com"
    assert result["name"] == "Test User"
    assert result["picture"] == "https://example.com/photo.jpg"
    # Verification is a gate, not a payload field — nothing downstream should be
    # able to mistake our verdict for an IdP assertion.
    assert "email_verified" not in result


@pytest.mark.asyncio
async def test_expired_token_rejected(rsa_keypair, make_token):
    """An expired OIDC token raises IdpValidationError with 'expired'."""
    _, public_key = rsa_keypair

    token = make_token(
        {
            "sub": "google-user-123",
            "email": "user@example.com",
            "name": "Test User",
            "email_verified": True,
        },
        exp_offset=-3600,  # expired 1 hour ago
    )

    with pytest.raises(IdpValidationError, match="expired"):
        await validate_idp_token(token, "google", _override_key=public_key)


@pytest.mark.asyncio
async def test_unsupported_provider_rejected():
    """An unsupported provider raises IdpValidationError with 'Unsupported'."""
    with pytest.raises(IdpValidationError, match="Unsupported"):
        await validate_idp_token("some-token", "myspace")


@pytest.mark.asyncio
async def test_unverified_email_rejected(rsa_keypair, make_token):
    """An OIDC token with email_verified=False raises with 'not verified'."""
    _, public_key = rsa_keypair

    token = make_token(
        {
            "sub": "google-user-456",
            "email": "unverified@example.com",
            "name": "Unverified User",
            "email_verified": False,
        }
    )

    with pytest.raises(IdpValidationError, match="not verified"):
        await validate_idp_token(token, "google", _override_key=public_key)


# ---------------------------------------------------------------------------
# Microsoft Entra ID.
#
# Regression: Entra emits NO `email_verified` claim (its analogue is the optional
# `xms_edov`), and omits `email` for managed work accounts unless the tenant adds
# it as an optional claim. A strict `email_verified is True` gate plus a bare
# payload["email"] therefore rejected/500'd every real Entra sign-in.
# ---------------------------------------------------------------------------

TEST_TENANT = "d3300fae-c9cd-404f-a259-b7f8e5f8998c"


@pytest.fixture
def entra_jwks(rsa_keypair, monkeypatch):
    """Point the entra_id provider at our test keypair via a mocked JWKS."""
    import json

    from jwt.algorithms import RSAAlgorithm

    from src.services import idp_validator

    _, public_key = rsa_keypair
    monkeypatch.setattr(settings, "entra_tenant_id", TEST_TENANT)
    monkeypatch.setattr(settings, "entra_client_id", "entra-client-id")
    idp_validator._jwks_cache["entra_id"] = (
        [json.loads(RSAAlgorithm.to_jwk(public_key))],
        time.monotonic(),
    )
    try:
        yield
    finally:
        idp_validator._jwks_cache.pop("entra_id", None)


def _entra_claims(**overrides) -> dict:
    """Claims as Entra actually issues them: no email_verified, tid present."""
    return {
        "sub": "entra-sub-1",
        "name": "Work User",
        "tid": TEST_TENANT,
        "iss": f"https://login.microsoftonline.com/{TEST_TENANT}/v2.0",
        "aud": "entra-client-id",
        "preferred_username": "user@tptdevelorg.onmicrosoft.com",
        **overrides,
    }


@pytest.mark.asyncio
async def test_entra_token_without_email_verified_is_accepted(make_token, entra_jwks):
    """Entra emits no email_verified; the pinned tenant is the verifying authority."""
    token = make_token(_entra_claims(email="user@tptdevelorg.onmicrosoft.com"))

    result = await validate_idp_token(token, "entra_id")

    assert result["sub"] == "entra-sub-1"
    assert result["email"] == "user@tptdevelorg.onmicrosoft.com"


@pytest.mark.asyncio
async def test_entra_token_falls_back_to_preferred_username(make_token, entra_jwks):
    """No `email` optional claim (the *.onmicrosoft default) — use the UPN."""
    token = make_token(_entra_claims())

    result = await validate_idp_token(token, "entra_id")

    assert result["email"] == "user@tptdevelorg.onmicrosoft.com"


@pytest.mark.asyncio
async def test_entra_token_with_xms_edov_false_rejected(make_token, entra_jwks):
    """xms_edov=False is Entra saying the email domain is NOT owner-verified."""
    token = make_token(_entra_claims(xms_edov=False))

    with pytest.raises(IdpValidationError, match="not verified"):
        await validate_idp_token(token, "entra_id")


@pytest.mark.asyncio
async def test_entra_token_from_other_tenant_rejected(make_token, entra_jwks):
    """A token whose tid is not our pinned tenant gets no email_verified bypass.

    Signature/issuer pinning already blocks foreign tenants; this asserts the
    claim-level gate does not become the weak link if issuer checking is ever
    loosened (e.g. multi-tenant `organizations`).
    """
    from src.services.auth_service import is_email_verified_claim

    assert (
        is_email_verified_claim(_entra_claims(tid="some-other-tenant"), "entra_id")
        is False
    )


@pytest.mark.asyncio
async def test_token_with_no_address_rejected_cleanly(make_token, entra_jwks):
    """No email and no address-shaped UPN => actionable error, not a KeyError/500."""
    token = make_token(_entra_claims(preferred_username="not-an-address"))

    with pytest.raises(IdpValidationError, match="no email address"):
        await validate_idp_token(token, "entra_id")


@pytest.mark.asyncio
async def test_google_still_requires_email_verified(
    rsa_keypair, make_token, monkeypatch
):
    """The Entra branch must not loosen Google: no tid claim => strict gate stands."""
    _, public_key = rsa_keypair
    monkeypatch.setattr(settings, "entra_tenant_id", TEST_TENANT)

    token = make_token(
        {"sub": "g1", "email": "u@x.test", "email_verified": False, "name": "G"}
    )

    with pytest.raises(IdpValidationError, match="not verified"):
        await validate_idp_token(token, "google", _override_key=public_key)


@pytest.mark.asyncio
async def test_another_issuer_cannot_borrow_entras_exemption(
    make_token, test_oidc_static, monkeypatch
):
    """A DIFFERENT trusted OIDC issuer minting `tid = <our Entra tenant>` must not
    inherit Entra's no-email_verified exemption.

    This is the end-to-end form of the shape-vs-provider trap: the token is validly
    signed for an issuer we trust (here the gated test_oidc seam; in a real
    deployment a self-hosted `dex`), and it simply asserts Entra's tenant claim.
    Trust is decided by the provider the signature was verified against, so the
    unverified email is rejected.
    """
    monkeypatch.setattr(settings, "entra_tenant_id", TEST_TENANT)
    token = make_token(
        {
            "sub": "u1",
            "email": "victim@corp.test",
            "tid": TEST_TENANT,  # borrowed claim
            "iss": "https://rogue.test",
            "aud": "deployment-wide-client",
        }
    )

    with pytest.raises(IdpValidationError, match="not verified"):
        await validate_idp_token(token, "test_oidc")


# ---------------------------------------------------------------------------
# GitHub OAuth-app-binding tests
#
# Regression: without a binding check, ANY valid GitHub access token (including
# one issued to an attacker-registered OAuth app after phishing a victim's
# consent) is accepted, because `GET /user` authenticates the underlying user
# regardless of which OAuth app holds the token. The fix consults GitHub's
# app-scoped introspection endpoint `POST /applications/{client_id}/token`,
# which returns 200 only when the token was issued to the authenticated app.
# ---------------------------------------------------------------------------


@pytest.fixture
def github_app_creds(monkeypatch):
    """Point settings at a known client_id/secret for the GitHub app check."""
    monkeypatch.setattr(settings, "github_client_id", "duar-client-id")
    monkeypatch.setattr(settings, "github_client_secret", "duar-secret")


@pytest.mark.asyncio
async def test_github_token_rejected_when_not_bound_to_duar_app(github_app_creds):
    """A GitHub token valid at /user but not issued to Duar's OAuth app is rejected.

    Attack path: attacker registers "EVIL-APP", phishes victim to authorize it,
    captures the resulting access token, submits it to /authz/resolve. GitHub's
    /user returns the victim's profile (the token IS valid), but GitHub's
    app-scoped introspection returns 404 (the token was not issued to Duar's
    app). Duar must fail closed here.
    """
    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://api.github.com/applications/duar-client-id/token").mock(
            return_value=httpx.Response(404)
        )
        mock.get("https://api.github.com/user").mock(
            return_value=httpx.Response(
                200, json={"id": 123, "login": "victim", "name": "Victim"}
            )
        )
        mock.get("https://api.github.com/user/emails").mock(
            return_value=httpx.Response(
                200,
                json=[{"primary": True, "verified": True, "email": "v@example.com"}],
            )
        )

        with pytest.raises(IdpValidationError, match="not issued"):
            await validate_idp_token("attacker-held-victim-token", "github")


@pytest.mark.asyncio
async def test_github_token_accepted_when_bound_to_duar_app(github_app_creds):
    """A GitHub token that passes the app-binding check authenticates normally."""
    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://api.github.com/applications/duar-client-id/token").mock(
            return_value=httpx.Response(200, json={"id": 42, "login": "user"})
        )
        mock.get("https://api.github.com/user").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 42,
                    "login": "user",
                    "name": "Legit User",
                    "avatar_url": "https://example.com/a.png",
                },
            )
        )
        mock.get("https://api.github.com/user/emails").mock(
            return_value=httpx.Response(
                200,
                json=[{"primary": True, "verified": True, "email": "user@example.com"}],
            )
        )

        result = await validate_idp_token("legit-duar-bound-token", "github")

        assert result["sub"] == "github|42"
        assert result["email"] == "user@example.com"
        assert result["name"] == "Legit User"


@pytest.mark.asyncio
async def test_github_token_binding_uses_basic_auth_with_client_secret(
    github_app_creds,
):
    """The app-binding request must authenticate with Basic(client_id:client_secret).

    GitHub's /applications/{id}/token endpoint is only accessible to the OAuth
    app itself — the authenticating principal is the app, not the token's user.
    Without Basic auth (or with the wrong secret), GitHub returns 401 and the
    check degrades to "always fail" regardless of token validity.
    """
    import base64

    captured_auth: dict = {}

    def _record_auth(request: httpx.Request) -> httpx.Response:
        captured_auth["header"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"id": 1, "login": "u"})

    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://api.github.com/applications/duar-client-id/token").mock(
            side_effect=_record_auth
        )
        mock.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"id": 1, "login": "u", "name": "U"})
        )
        mock.get("https://api.github.com/user/emails").mock(
            return_value=httpx.Response(
                200, json=[{"primary": True, "verified": True, "email": "u@e.com"}]
            )
        )

        await validate_idp_token("t", "github")

    expected = "Basic " + base64.b64encode(b"duar-client-id:duar-secret").decode()
    assert captured_auth["header"] == expected, (
        "Binding check must authenticate as the OAuth app via HTTP Basic; "
        "otherwise GitHub rejects the request regardless of token validity."
    )


# ---------------------------------------------------------------------------
# Gated static-key test_oidc provider (Layer-2 trust-boundary pentest seam).
# Off in prod (empty pubkey => unregistered => fail closed). Validates exactly like a real
# OIDC provider but against a statically-configured public key.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gated_test_oidc_provider_validates_static_key(
    rsa_keypair, make_token, monkeypatch
):
    """When configured, test_oidc validates tokens signed by the static key and enforces
    issuer/audience/email_verified; forged or unverified tokens are rejected."""
    from src.services import idp_validator

    _, public_key = rsa_keypair
    pub_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    monkeypatch.setenv("TEST_TRUSTED_ISSUER_PUBKEY", pub_pem)
    monkeypatch.setenv("TEST_TRUSTED_ISSUER", "https://rogue.test")
    monkeypatch.setenv("TEST_TRUSTED_AUDIENCE", "sentinel:authz")
    idp_validator._register_test_provider()
    try:
        good = make_token(
            {
                "sub": "u1",
                "email": "u1@x.test",
                "email_verified": True,
                "iss": "https://rogue.test",
                "aud": "sentinel:authz",
            }
        )
        result = await validate_idp_token(good, "test_oidc")
        assert result["sub"] == "u1"

        unverified = make_token(
            {
                "sub": "u1",
                "email": "u1@x.test",
                "email_verified": False,
                "iss": "https://rogue.test",
                "aud": "sentinel:authz",
            }
        )
        with pytest.raises(IdpValidationError, match="not verified"):
            await validate_idp_token(unverified, "test_oidc")

        # forged: signed by a DIFFERENT key -> signature must be rejected
        attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        attacker_pem = attacker.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        now = int(time.time())
        forged = pyjwt.encode(
            {
                "sub": "u1",
                "email": "u1@x.test",
                "email_verified": True,
                "iss": "https://rogue.test",
                "aud": "sentinel:authz",
                "iat": now,
                "exp": now + 3600,
            },
            attacker_pem,
            algorithm="RS256",
        )
        with pytest.raises(IdpValidationError, match="Invalid token"):
            await validate_idp_token(forged, "test_oidc")
    finally:
        idp_validator._PROVIDER_CONFIG.pop("test_oidc", None)


@pytest.mark.asyncio
async def test_test_oidc_provider_absent_by_default():
    """With no pubkey configured (prod default), test_oidc is unregistered => fail closed."""
    with pytest.raises(IdpValidationError, match="Unsupported"):
        await validate_idp_token("x.y.z", "test_oidc")


# ---------------------------------------------------------------------------
# Per-app audience binding (expected_audiences).
#
# Regression: the id_token's audience is otherwise validated only against the
# single, deployment-wide provider client_id. So a token minted for app A is
# accepted no matter which app's service key presents it — a stolen id_token
# works at *any* app's mint route. expected_audiences lets /authz/resolve bind
# the token to the CALLING app's registered IdP client_id(s): a token whose aud
# matches the deployment-wide audience is still rejected when the caller's
# registered audience differs.
# ---------------------------------------------------------------------------


@pytest.fixture
def test_oidc_static(rsa_keypair, monkeypatch):
    """Register the gated static-key test_oidc provider with a known deployment audience."""
    from src.services import idp_validator

    _, public_key = rsa_keypair
    pub_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    monkeypatch.setenv("TEST_TRUSTED_ISSUER_PUBKEY", pub_pem)
    monkeypatch.setenv("TEST_TRUSTED_ISSUER", "https://rogue.test")
    monkeypatch.setenv("TEST_TRUSTED_AUDIENCE", "deployment-wide-client")
    idp_validator._register_test_provider()
    try:
        yield
    finally:
        idp_validator._PROVIDER_CONFIG.pop("test_oidc", None)


@pytest.mark.asyncio
async def test_expected_audiences_rejects_token_minted_for_another_app(
    make_token, test_oidc_static
):
    """A token whose aud matches the deployment-wide provider audience is REJECTED when the
    calling app's registered audience differs (cross-app replay containment)."""
    token = make_token(
        {
            "sub": "u1",
            "email": "u1@x.test",
            "email_verified": True,
            "iss": "https://rogue.test",
            "aud": "deployment-wide-client",
        }
    )
    with pytest.raises(IdpValidationError):
        await validate_idp_token(
            token, "test_oidc", expected_audiences=["some-other-apps-client"]
        )


@pytest.mark.asyncio
async def test_expected_audiences_accepts_token_for_the_calling_app(
    make_token, test_oidc_static
):
    """When the token's aud is in the calling app's registered audiences, it validates."""
    token = make_token(
        {
            "sub": "u1",
            "email": "u1@x.test",
            "email_verified": True,
            "iss": "https://rogue.test",
            "aud": "deployment-wide-client",
        }
    )
    result = await validate_idp_token(
        token, "test_oidc", expected_audiences=["deployment-wide-client"]
    )
    assert result["sub"] == "u1"


@pytest.mark.asyncio
async def test_no_expected_audiences_falls_back_to_provider_audience(
    make_token, test_oidc_static
):
    """No registered audience (unset, the default) => fall back to the deployment-wide
    provider audience: today's behavior is preserved, nothing breaks on upgrade."""
    token = make_token(
        {
            "sub": "u1",
            "email": "u1@x.test",
            "email_verified": True,
            "iss": "https://rogue.test",
            "aud": "deployment-wide-client",
        }
    )
    result = await validate_idp_token(token, "test_oidc")
    assert result["sub"] == "u1"


# ---------------------------------------------------------------------------
# GitHub × per-app audience binding — fail closed, never silently unbound
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_github_rejected_when_app_configures_audience_binding():
    """GitHub tokens are opaque — a configured per-app audience binding must
    fail closed rather than silently skip enforcement."""
    with pytest.raises(IdpValidationError, match="cannot be enforced for GitHub"):
        await validate_idp_token("gho_x", "github", expected_audiences=["client-a"])


# ---------------------------------------------------------------------------
# JWKS rotate-in: one cache-busting refetch instead of failing until TTL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_jwks_refetch_on_unknown_key(rsa_keypair, make_token, monkeypatch):
    """A token signed by a key rotated in after the cached JWKS snapshot must
    trigger one rate-limited refetch instead of failing until the 1h TTL lapses."""
    import json

    from jwt.algorithms import RSAAlgorithm

    from src.services import idp_validator

    _, public_key = rsa_keypair
    good_jwk = json.loads(RSAAlgorithm.to_jwk(public_key))
    stale_pub = rsa.generate_private_key(
        public_exponent=65537, key_size=2048
    ).public_key()
    stale_jwk = json.loads(RSAAlgorithm.to_jwk(stale_pub))

    # Cached snapshot: within the 1h TTL but past the 60s forced-refresh floor
    idp_validator._jwks_cache["google"] = ([stale_jwk], time.monotonic() - 120)
    respx.get("https://www.googleapis.com/oauth2/v3/certs").mock(
        return_value=httpx.Response(200, json={"keys": [good_jwk]})
    )
    monkeypatch.setattr(settings, "google_client_id", "gcid")

    token = make_token(
        {
            "sub": "u1",
            "email": "u@x.test",
            "email_verified": True,
            "iss": "https://accounts.google.com",
            "aud": "gcid",
        }
    )
    try:
        result = await validate_idp_token(token, "google")
    finally:
        idp_validator._jwks_cache.pop("google", None)
    assert result["sub"] == "u1"
