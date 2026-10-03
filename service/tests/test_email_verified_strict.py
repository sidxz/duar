"""Regression test: ``email_verified`` must be strictly the boolean ``True``.

Vulnerability: the proxy-mode callbacks in ``auth_routes.py`` used
``if not userinfo.get("email_verified", False):``, which accepts any truthy
value — including the *string* ``"false"``. An IdP that emits a stringified
boolean (a known quirk of some configurations) could therefore slip an
unverified-email sign-in past the check. V13 fixed this in
``idp_validator.py`` (authz-mode) but the proxy-mode paths used a separate
inline check that was missed. The shared helper below closes the scope gap.

The second class covers the ONE documented exemption: Entra ID emits no
``email_verified`` claim at all, so tokens from the single pinned tenant are
accepted on the strength of the issuer pin. That exemption must stay welded to
the verified provider — never inferred from claim shape.
"""

from __future__ import annotations

import pytest

from src.config import settings
from src.services.auth_service import is_email_verified_claim

TENANT = "11111111-2222-3333-4444-555555555555"


class TestIsEmailVerifiedClaim:
    def test_boolean_true_accepted(self):
        assert is_email_verified_claim({"email_verified": True}, "google") is True

    def test_boolean_false_rejected(self):
        assert is_email_verified_claim({"email_verified": False}, "google") is False

    @pytest.mark.parametrize("truthy_string", ["true", "True", "TRUE", "1", "yes"])
    def test_truthy_strings_rejected(self, truthy_string):
        assert (
            is_email_verified_claim({"email_verified": truthy_string}, "google")
            is False
        )

    @pytest.mark.parametrize("falsy_string", ["false", "False", "0", "no", ""])
    def test_any_string_rejected(self, falsy_string):
        """Even a 'false' string is a non-boolean — reject strictly."""
        assert (
            is_email_verified_claim({"email_verified": falsy_string}, "google") is False
        )

    def test_integer_one_rejected(self):
        assert is_email_verified_claim({"email_verified": 1}, "google") is False

    def test_missing_rejected(self):
        assert is_email_verified_claim({}, "google") is False

    def test_none_rejected(self):
        assert is_email_verified_claim({"email_verified": None}, "google") is False


class TestEntraTenantExemption:
    """Entra has no ``email_verified`` claim; the pinned tenant vouches instead."""

    @pytest.fixture(autouse=True)
    def _pin_tenant(self, monkeypatch):
        monkeypatch.setattr(settings, "entra_tenant_id", TENANT)

    def test_pinned_tenant_accepted_without_the_claim(self):
        assert is_email_verified_claim({"tid": TENANT}, "entra_id") is True

    def test_xms_edov_false_rejected(self):
        """Entra explicitly saying the domain is NOT owner-verified overrides the pin."""
        assert (
            is_email_verified_claim({"tid": TENANT, "xms_edov": False}, "entra_id")
            is False
        )

    def test_xms_edov_true_accepted(self):
        assert (
            is_email_verified_claim({"tid": TENANT, "xms_edov": True}, "entra_id")
            is True
        )

    def test_foreign_tenant_rejected(self):
        """Only the tenant this deployment pins gets the exemption."""
        assert is_email_verified_claim({"tid": "other-tenant"}, "entra_id") is False

    def test_missing_tid_rejected(self):
        assert is_email_verified_claim({}, "entra_id") is False

    def test_unconfigured_entra_rejected(self, monkeypatch):
        """No ENTRA_TENANT_ID => no pin to trust => strict behaviour, fail closed."""
        monkeypatch.setattr(settings, "entra_tenant_id", "")
        assert is_email_verified_claim({"tid": ""}, "entra_id") is False

    @pytest.mark.parametrize("provider", ["google", "github", "dex", "test_oidc"])
    def test_other_providers_cannot_inherit_the_exemption(self, provider):
        """The exemption is keyed on the provider the token was VERIFIED against,
        not on a `tid` claim inside it.

        Attack this closes: any other OIDC issuer we trust (a self-hosted `dex`, a
        future IdP, the gated test_oidc seam) could mint a token carrying
        `tid = <our Entra tenant GUID>` and no `email_verified`, and a shape-based
        check would hand it Entra's exemption — an unverified email accepted from
        an issuer that never verified it.
        """
        assert is_email_verified_claim({"tid": TENANT}, provider) is False
