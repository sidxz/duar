"""JWKS key resolution must not block the event loop.

PyJWKClient.get_signing_key_from_jwt does a synchronous urllib fetch on JWKS
cache expiry (~every 5 min) or an unknown kid (key rotation). Called inline in
``async def dispatch``, that stalls EVERY in-flight request on the worker for
up to the fetch timeout — it must run in a worker thread instead.
"""

import asyncio
import time

import httpx
from fastapi import FastAPI
from jwt.exceptions import PyJWKClientError

from duar_auth.authz_middleware import AuthzMiddleware
from duar_auth.middleware import JWTAuthMiddleware

_SLEEP = 0.25
_MIN_TICKS = 5  # the loop must keep turning while the "fetch" sleeps


async def _request_while_ticking(app) -> tuple[int, httpx.Response]:
    ticks = 0

    async def _ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    task = asyncio.create_task(_ticker())
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/anything",
                headers={"Authorization": "Bearer x", "X-Authz-Token": "y"},
            )
    finally:
        task.cancel()
    return ticks, resp


async def test_jwt_middleware_key_fetch_off_loop(monkeypatch):
    def _slow_key(self, token):
        time.sleep(_SLEEP)  # simulates the sync urllib JWKS fetch
        raise PyJWKClientError("no key")

    monkeypatch.setattr(JWTAuthMiddleware, "_signing_key", _slow_key)
    app = FastAPI()
    app.add_middleware(JWTAuthMiddleware, jwks_url="https://duar.test/jwks.json")

    ticks, resp = await _request_while_ticking(app)
    assert resp.status_code == 401
    assert ticks >= _MIN_TICKS, f"event loop was blocked (ticks={ticks})"


async def test_authz_middleware_idp_decode_off_loop(monkeypatch):
    def _slow_decode(self, token):
        time.sleep(_SLEEP)
        raise PyJWKClientError("no key")

    monkeypatch.setattr(AuthzMiddleware, "_decode_idp_token", _slow_decode)
    app = FastAPI()
    app.add_middleware(
        AuthzMiddleware,
        service_name="svc",
        idp_audience="aud",
        idp_jwks_url="https://idp.test/jwks.json",
        duar_public_key="dummy-pem",  # decode paths are monkeypatched
    )

    ticks, resp = await _request_while_ticking(app)
    assert resp.status_code == 401
    assert ticks >= _MIN_TICKS, f"event loop was blocked (ticks={ticks})"


async def test_authz_middleware_duar_decode_off_loop(monkeypatch):
    def _fast_idp(self, token):
        return {"sub": "abc"}

    def _slow_authz(self, token):
        time.sleep(_SLEEP)
        raise PyJWKClientError("no key")

    monkeypatch.setattr(AuthzMiddleware, "_decode_idp_token", _fast_idp)
    monkeypatch.setattr(AuthzMiddleware, "_decode_authz", _slow_authz)
    app = FastAPI()
    app.add_middleware(
        AuthzMiddleware,
        service_name="svc",
        idp_audience="aud",
        idp_jwks_url="https://idp.test/jwks.json",
        duar_public_key="dummy-pem",  # decode paths are monkeypatched
    )

    ticks, resp = await _request_while_ticking(app)
    assert resp.status_code == 401
    assert ticks >= _MIN_TICKS, f"event loop was blocked (ticks={ticks})"
