"""Rate limiting must be disableable for the ephemeral pentest target.

The Layer-2 isolation prover drives many OAuth logins in a burst, tripping the
per-route limits. For a throwaway, allowlisted target the limiter is bypassable
via RATE_LIMIT_ENABLED=false — without weakening the production default (on).
Fail-safe: the field defaults to True and the Limiter is constructed with
enabled=that value, so slowapi short-circuits every check when off.
"""

from fastapi import FastAPI, Request
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.testclient import TestClient

import src.middleware.rate_limit as rl
from src.config import settings


def test_disabled_limiter_bypasses_checks():
    lim = Limiter(key_func=rl.get_client_ip, storage_uri="memory://", enabled=False)
    app = FastAPI()
    app.state.limiter = lim
    app.add_exception_handler(RateLimitExceeded, rl.rate_limit_exceeded_handler)

    @app.get("/probe")
    @lim.limit("1/minute")
    async def probe(request: Request):
        return {"ok": True}

    client = TestClient(app)
    # Well past 1/minute; every call passes because the limiter is off (no store hit).
    for _ in range(5):
        assert client.get("/probe").status_code == 200


def test_singleton_limiter_honors_enabled_setting():
    assert rl.limiter.enabled == settings.rate_limit_enabled


def test_rate_limiting_enabled_by_default():
    assert settings.rate_limit_enabled is True
