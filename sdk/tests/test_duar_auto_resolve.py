"""Duar(auto_resolve=True): validation and protect() pass-through to AuthzMiddleware."""

import pytest
from starlette.applications import Starlette

from duar_auth import Duar


def _duar(public_pem: str, **kw) -> Duar:
    return Duar(
        base_url="https://duar.test",
        service_name="reports",
        service_key="svc-key",
        idp_public_key=public_pem,
        idp_audience="my-client-id",
        **kw,
    )


def test_auto_resolve_requires_idp_provider(rsa_keypair):
    _, public_pem = rsa_keypair
    with pytest.raises(ValueError, match="idp_provider"):
        _duar(public_pem, auto_resolve=True)


def test_protect_passes_auto_resolve_to_middleware(rsa_keypair):
    _, public_pem = rsa_keypair
    app = Starlette(routes=[])
    _duar(public_pem, auto_resolve=True, idp_provider="google").protect(app)
    kwargs = app.user_middleware[0].kwargs
    assert kwargs["auto_resolve"] is True
    assert kwargs["idp_provider"] == "google"


def test_auto_resolve_defaults_off(rsa_keypair):
    _, public_pem = rsa_keypair
    app = Starlette(routes=[])
    _duar(public_pem).protect(app)
    assert app.user_middleware[0].kwargs["auto_resolve"] is False


def test_auto_resolve_rejected_in_proxy_mode():
    with pytest.raises(ValueError, match="mode='authz'"):
        Duar(
            base_url="https://duar.test",
            service_name="reports",
            service_key="svc-key",
            mode="proxy",
            auto_resolve=True,
            idp_provider="google",
        )
