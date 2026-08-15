"""Dex OIDC provider — config-gated, inert unless ``DEX_CLIENT_ID`` is set.

A self-hosted Dex is the OIDC provider the Layer-2 isolation prover drives for faithful
token issuance (real issuance exercises the issuance-time org gate, which is what makes
org isolation testable). ``get_configured_providers`` reads ``settings`` at call time, so
the gate is exercised by toggling the singleton's attributes — matching the project's
existing settings-override convention (see ``test_key_rotation.py``).
"""

from src.auth.providers import get_configured_providers
from src.config import settings


def test_dex_listed_among_providers_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "dex_client_id", "duar-prover")
    monkeypatch.setattr(
        settings,
        "dex_server_metadata_url",
        "http://dex:5556/.well-known/openid-configuration",
    )
    assert "dex" in get_configured_providers()


def test_dex_inert_when_unconfigured(monkeypatch):
    # Fail-safe: the production default (no DEX_* set) must not advertise the provider.
    monkeypatch.setattr(settings, "dex_client_id", "")
    monkeypatch.setattr(settings, "dex_server_metadata_url", "")
    assert "dex" not in get_configured_providers()
