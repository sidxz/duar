"""Duar.verify_m2m_token: accept a no-user realm token -> SystemAuth."""

import datetime
import uuid

import jwt as pyjwt
import pytest

from duar_auth import Duar, SystemAuth
from duar_auth.types import DuarError


def _duar(public_pem: str, *, effective_scope: str = "acme-suite", service_name: str = "reports") -> Duar:
    s = Duar(
        base_url="https://duar.test",
        service_name=service_name,
        service_key="svc-key",
        idp_public_key="-----BEGIN PUBLIC KEY-----\nx\n-----END PUBLIC KEY-----",
        idp_audience="my-client-id",
    )
    s._duar_public_key = public_pem  # normally set by lifespan
    s._effective_scope = effective_scope
    return s


def _m2m(
    private_pem: str,
    *,
    svc="acme-suite",
    caller="docs",
    actions=None,
    aud="sentinel:m2m",
    typ="m2m",
    aud_target=None,
    ttl=300,
) -> str:
    now = datetime.datetime.now(datetime.UTC)
    return pyjwt.encode(
        {
            "iss": "https://duar.test",
            "aud": aud,
            "type": typ,
            "svc": svc,
            "caller": caller,
            "actions": actions if actions is not None else ["*"],
            "aud_target": aud_target,
            "jti": str(uuid.uuid4()),
            "iat": now,
            "exp": now + datetime.timedelta(seconds=ttl),
        },
        private_pem,
        algorithm="RS256",
    )


def test_accepts_valid_m2m_token(rsa_keypair):
    private_pem, public_pem = rsa_keypair
    s = _duar(public_pem)
    sys_auth = s.verify_m2m_token(_m2m(private_pem))
    assert isinstance(sys_auth, SystemAuth)
    assert sys_auth.caller == "docs"
    assert sys_auth.svc == "acme-suite"
    assert sys_auth.can("anything") is True


def test_rejects_cross_realm_svc(rsa_keypair):
    private_pem, public_pem = rsa_keypair
    s = _duar(public_pem, effective_scope="acme-suite")
    with pytest.raises(DuarError) as exc:
        s.verify_m2m_token(_m2m(private_pem, svc="other-realm"))
    assert exc.value.status_code == 403


def test_rejects_wrong_audience(rsa_keypair):
    private_pem, public_pem = rsa_keypair
    s = _duar(public_pem)
    # A user authz token (aud=sentinel:authz) must never validate as m2m.
    with pytest.raises(DuarError) as exc:
        s.verify_m2m_token(_m2m(private_pem, aud="sentinel:authz", typ="authz"))
    assert exc.value.status_code == 401


def test_rejects_expired(rsa_keypair):
    private_pem, public_pem = rsa_keypair
    s = _duar(public_pem)
    with pytest.raises(DuarError):
        s.verify_m2m_token(_m2m(private_pem, ttl=-10))


def test_aud_target_must_match_when_set(rsa_keypair):
    private_pem, public_pem = rsa_keypair
    s = _duar(public_pem, service_name="reports")
    # aud_target narrows the token to one service; reports != billing -> reject.
    with pytest.raises(DuarError) as exc:
        s.verify_m2m_token(_m2m(private_pem, aud_target="billing"))
    assert exc.value.status_code == 403
    # ...and is accepted when it matches this service.
    ok = s.verify_m2m_token(_m2m(private_pem, aud_target="reports"))
    assert ok.svc == "acme-suite"


def test_raises_when_public_key_missing(rsa_keypair):
    private_pem, _ = rsa_keypair
    s = Duar(
        base_url="https://duar.test", service_name="reports", service_key="k", idp_public_key="x", idp_audience="a"
    )
    with pytest.raises(DuarError) as exc:
        s.verify_m2m_token(_m2m(private_pem))
    assert exc.value.status_code == 503
