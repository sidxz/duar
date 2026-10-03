"""Every configured rate-limit tier must be a string the `limits` library accepts.
Guards against typos like "10/min" (valid format is "10/minute") that would
otherwise blow up at request time inside slowapi.
"""

import pytest
from limits import parse
from pydantic import ValidationError

from src.config import Settings, settings

TIERS = [
    "rate_limit_default",
    "rate_limit_aggregate",
    "rate_limit_auth",
    "rate_limit_auth_admin",
    "rate_limit_authz_resolve",
    "rate_limit_read",
    "rate_limit_admin_write",
    "rate_limit_sensitive",
]

# Per-route (decorated) tiers — "" here means @limiter.limit("") = unlimited +
# exempt from the aggregate, so it must be rejected at startup.
DECORATED_TIERS = [
    "rate_limit_auth",
    "rate_limit_auth_admin",
    "rate_limit_authz_resolve",
    "rate_limit_read",
    "rate_limit_admin_write",
    "rate_limit_sensitive",
]


def test_all_tiers_exist_and_parse():
    for name in TIERS:
        value = getattr(settings, name)
        assert isinstance(value, str), name
        if value:  # "" is the explicit "disable this tier" duar
            parse(value)  # raises ValueError on a malformed limit string


@pytest.mark.parametrize("name", DECORATED_TIERS)
def test_empty_decorated_tier_is_rejected(name):
    # The footgun: "" on a decorated route silently disables throttling.
    with pytest.raises(ValidationError):
        Settings(**{name: ""})


@pytest.mark.parametrize("name", DECORATED_TIERS)
def test_malformed_decorated_tier_is_rejected(name):
    with pytest.raises(ValidationError):
        Settings(**{name: "10/min"})  # invalid limits format


def test_empty_aggregate_and_default_are_allowed():
    # Only the middleware-level tiers may be disabled via "".
    s = Settings(rate_limit_aggregate="", rate_limit_default="")
    assert s.rate_limit_aggregate == ""
    assert s.rate_limit_default == ""
