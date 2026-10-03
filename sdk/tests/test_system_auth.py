"""SystemAuth — the no-user (m2m) in-realm caller context."""

from duar_auth import SystemAuth


def test_full_trust_actions_star_allows_anything():
    sys_auth = SystemAuth(caller="docs", actions=["*"], svc="acme-suite")
    assert sys_auth.can("reports:export") is True
    assert sys_auth.can("anything") is True


def test_specific_action_allowed_and_denied():
    sys_auth = SystemAuth(caller="docs", actions=["reports:read"], svc="acme-suite")
    assert sys_auth.can("reports:read") is True
    assert sys_auth.can("reports:write") is False


def test_carries_caller_and_svc_no_user():
    sys_auth = SystemAuth(caller="docs", actions=["*"], svc="acme-suite")
    assert sys_auth.caller == "docs"
    assert sys_auth.svc == "acme-suite"
    # No user identity on a SystemAuth — it is an honest "no human" context.
    assert not hasattr(sys_auth, "user")
