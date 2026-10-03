"""The 429 path must still emit the ``ratelimit.exceeded`` security event.

With GlobalRateLimitMiddleware removed, slowapi is the only source of 429s; the
shared ``rate_limit_exceeded_handler`` logs the event for every limit (route,
default, and aggregate).
"""

import pytest
from starlette.requests import Request
from structlog.testing import capture_logs

import src.middleware.rate_limit as rl


def _request(path="/admin/x", ip="10.0.0.9"):
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "client": (ip, 5555),
            "server": ("t", 80),
        }
    )


def _exc(scope):
    # Build a REAL RateLimitExceeded — the handler reads exc.limit.limit.get_expiry()
    # for Retry-After (slowapi's exception has no .retry_after) and exc.limit.scope
    # for the reason. A fake stub would mask both paths.
    from limits import parse
    from slowapi.errors import RateLimitExceeded
    from slowapi.wrappers import Limit

    return RateLimitExceeded(
        Limit(parse("5/minute"), lambda: "k", scope, False, None, None, None, 1, True)
    )


@pytest.mark.asyncio
async def test_handler_emits_ratelimit_exceeded():
    with capture_logs() as logs:
        resp = await rl.rate_limit_exceeded_handler(_request(), _exc(scope=None))

    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) > 0
    events = [e for e in logs if e["event"] == "ratelimit.exceeded"]
    assert len(events) == 1
    assert events[0]["outcome"] == "denied"
    assert events[0]["reason"] == "route_limit"  # non-global scope -> per-route
    assert events[0]["http.route"] == "/admin/x"
    assert events[0]["source_ip"] == "10.0.0.9"


@pytest.mark.asyncio
async def test_aggregate_breach_logged_as_aggregate():
    # application_limits carry scope="global"; the handler must label these
    # distinctly from per-route limits so alerting can separate volumetric abuse.
    with capture_logs() as logs:
        await rl.rate_limit_exceeded_handler(_request(), _exc(scope="global"))

    events = [e for e in logs if e["event"] == "ratelimit.exceeded"]
    assert len(events) == 1
    assert events[0]["reason"] == "aggregate"
