"""Tier-1 security-signal tests: centroids, travel, first-seen, stuffing."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import settings
from src.services.country_centroids import CENTROIDS, centroid_km, haversine_km


class TestCentroids:
    def test_haversine_known_pair(self):
        # NYC ↔ London ≈ 5570 km
        nyc, london = (40.71, -74.0), (51.5, -0.13)
        assert haversine_km(nyc, london) == pytest.approx(5570, rel=0.02)

    def test_centroid_km_us_ru_is_far(self):
        km = centroid_km("US", "RU")
        assert km is not None and km > 6000

    def test_same_country_is_zero(self):
        assert centroid_km("DE", "DE") == 0

    def test_unknown_code_returns_none(self):
        assert centroid_km("US", "ZZ") is None
        assert centroid_km("ZZ", "US") is None

    def test_table_is_plausible(self):
        # Every entry is a valid lat/lon; majors are present.
        for cc, (lat, lon) in CENTROIDS.items():
            assert len(cc) == 2 and -90 <= lat <= 90 and -180 <= lon <= 180
        for major in ("US", "GB", "DE", "FR", "IN", "CN", "BR", "JP", "AU", "RU"):
            assert major in CENTROIDS


class FakeRedis:
    """Minimal async-redis stand-in: kv + sets, TTLs ignored."""

    def __init__(self):
        self.kv: dict = {}
        self.sets: dict[str, set] = {}

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, ex=None, nx=False):
        if nx and k in self.kv:
            return None
        self.kv[k] = v
        return True

    async def sadd(self, k, m):
        s = self.sets.setdefault(k, set())
        if m in s:
            return 0
        s.add(m)
        return 1

    async def scard(self, k):
        return len(self.sets.get(k, set()))

    async def incr(self, k):
        self.kv[k] = int(self.kv.get(k, 0)) + 1
        return self.kv[k]

    async def expire(self, k, ttl):
        return True


NOW = 1_700_000_000.0
UID = uuid.uuid4()


def _patched(fake, geo):
    """Patch redis, geoip, and clock; return the log_activity patcher too."""
    from src.services import signal_service

    return (
        patch.object(signal_service, "get_redis", AsyncMock(return_value=fake)),
        patch.object(signal_service, "_lookup_country", lambda ip: geo),
        patch.object(signal_service, "_now", lambda: NOW),
        patch("src.services.activity_service.log_activity", new_callable=AsyncMock),
    )


@pytest.mark.asyncio
async def test_travel_signal_fires_on_fast_country_jump():
    from src.services import signal_service

    fake = FakeRedis()
    # Seen in the US one hour ago; now "in" Russia (≈7600 km) → ≈7600 km/h.
    fake.kv[f"geo:last:{UID}"] = json.dumps({"country": "US", "ts": NOW - 3600})
    p1, p2, p3, p4 = _patched(fake, ("RU", "Russia"))
    with p1, p2, p3, p4 as log_activity:
        await signal_service.on_login_success(
            AsyncMock(), user_id=UID, ip="203.0.113.9", user_agent="UA"
        )
    calls = [c.kwargs for c in log_activity.await_args_list]
    travel = [k for k in calls if k["action"] == "login_impossible_travel"]
    assert len(travel) == 1
    d = travel[0]["detail"]
    assert d["signal"] == "impossible_travel" and d["severity"] == "high"
    assert d["prev_country"] == "US" and d["country"] == "RU"
    assert d["kmh"] > settings.signal_impossible_travel_kmh
    assert travel[0]["target_id"] == UID and travel[0]["actor_id"] == UID


@pytest.mark.asyncio
async def test_travel_quiet_below_threshold_and_updates_baseline():
    from src.services import signal_service

    fake = FakeRedis()
    # US → GB over 24h ≈ 290 km/h — plausible.
    fake.kv[f"geo:last:{UID}"] = json.dumps({"country": "US", "ts": NOW - 86400})
    p1, p2, p3, p4 = _patched(fake, ("GB", "United Kingdom"))
    with p1, p2, p3, p4 as log_activity:
        await signal_service.on_login_success(
            AsyncMock(), user_id=UID, ip="203.0.113.9", user_agent="UA"
        )
    assert not [
        c
        for c in log_activity.await_args_list
        if c.kwargs["action"] == "login_impossible_travel"
    ]
    # Baseline moved to GB regardless.
    assert json.loads(fake.kv[f"geo:last:{UID}"])["country"] == "GB"


@pytest.mark.asyncio
async def test_travel_pair_flag_damps_pingpong():
    from src.services import signal_service

    fake = FakeRedis()
    p1, p2, p3, p4 = _patched(fake, ("RU", "Russia"))
    with p1, p2, p3, p4 as log_activity:
        for _ in range(2):
            fake.kv[f"geo:last:{UID}"] = json.dumps({"country": "US", "ts": NOW - 60})
            await signal_service.on_login_success(
                AsyncMock(), user_id=UID, ip="203.0.113.9", user_agent="UA"
            )
    travel = [
        c
        for c in log_activity.await_args_list
        if c.kwargs["action"] == "login_impossible_travel"
    ]
    assert len(travel) == 1  # second US↔RU flip inside the flag TTL is quiet


@pytest.mark.asyncio
async def test_first_sighting_seeds_silently():
    from src.services import signal_service

    fake = FakeRedis()
    p1, p2, p3, p4 = _patched(fake, ("US", "United States"))
    with p1, p2, p3, p4 as log_activity:
        await signal_service.on_login_success(
            AsyncMock(), user_id=UID, ip="203.0.113.9", user_agent="UA"
        )
    assert not [
        c
        for c in log_activity.await_args_list
        if c.kwargs["action"] == "login_impossible_travel"
    ]
    assert json.loads(fake.kv[f"geo:last:{UID}"])["country"] == "US"


@pytest.mark.asyncio
async def test_unresolvable_ip_is_quiet():
    from src.services import signal_service

    fake = FakeRedis()
    p1, p2, p3, p4 = _patched(fake, None)  # private/unresolvable
    with p1, p2, p3, p4 as log_activity:
        await signal_service.on_login_success(
            AsyncMock(), user_id=UID, ip="10.0.0.1", user_agent="UA"
        )
    assert not [
        c
        for c in log_activity.await_args_list
        if c.kwargs["action"] == "login_impossible_travel"
    ]


@pytest.mark.asyncio
async def test_fail_open_on_redis_error():
    from src.services import signal_service

    with (
        patch.object(signal_service, "get_redis", AsyncMock(side_effect=RuntimeError)),
        patch(
            "src.services.activity_service.log_activity", new_callable=AsyncMock
        ) as log_activity,
    ):
        # Must not raise.
        await signal_service.on_login_success(
            AsyncMock(), user_id=UID, ip="203.0.113.9", user_agent="UA"
        )
    log_activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_flag_short_circuits(monkeypatch):
    from src.services import signal_service

    monkeypatch.setattr(settings, "signals_enabled", False)
    boom = AsyncMock(side_effect=AssertionError("redis must not be touched"))
    with patch.object(signal_service, "get_redis", boom):
        await signal_service.on_login_success(
            AsyncMock(), user_id=UID, ip="203.0.113.9", user_agent="UA"
        )


@pytest.mark.asyncio
async def test_travel_signal_emits_stream_event():
    from src.services import signal_service

    fake = FakeRedis()
    # Seen in the US one hour ago; now "in" Russia (≈7600 km) → ≈7600 km/h.
    fake.kv[f"geo:last:{UID}"] = json.dumps({"country": "US", "ts": NOW - 3600})
    p1, p2, p3, p4 = _patched(fake, ("RU", "Russia"))
    with (
        p1,
        p2,
        p3,
        p4,
        patch.object(signal_service, "log_security") as log_security_mock,
    ):
        await signal_service.on_login_success(
            AsyncMock(), user_id=UID, ip="203.0.113.9", user_agent="Mozilla/5.0"
        )
    # Verify stream event was emitted with correct parameters
    log_security_mock.assert_called_once()
    call_args = log_security_mock.call_args
    assert call_args[0][0] == "auth.signal.impossible_travel"
    assert call_args[1]["outcome"] == "anomaly"
    assert call_args[1]["severity"] == "high"
    assert call_args[1]["source_ip"] == "203.0.113.9"
    assert call_args[1]["actor"] == str(UID)
    assert "user_agent" not in call_args[1]  # Excluded from stream
    assert call_args[1]["level"] == "warning"  # high severity streams at warning


@pytest.mark.asyncio
async def test_fail_open_heals_session_after_emit_error():
    """If _emit's DB work (log_activity/commit) blows up, on_login_success must
    still fail open AND leave the session usable for whatever the caller does
    next (e.g. ClientApp re-validation) — not stuck in pending-rollback."""
    from src.services import signal_service

    fake = FakeRedis()
    # Seed a fast US -> RU jump so the travel rule fires and reaches _emit.
    fake.kv[f"geo:last:{UID}"] = json.dumps({"country": "US", "ts": NOW - 3600})
    p1, p2, p3, _p4 = _patched(fake, ("RU", "Russia"))
    db = AsyncMock()
    with (
        p1,
        p2,
        p3,
        patch(
            "src.services.activity_service.log_activity",
            new_callable=AsyncMock,
            side_effect=RuntimeError("db down"),
        ),
    ):
        # Must not raise.
        await signal_service.on_login_success(
            db, user_id=UID, ip="203.0.113.9", user_agent="UA"
        )
    db.rollback.assert_awaited()


@pytest.mark.asyncio
async def test_new_country_signal_after_seed():
    from src.services import signal_service

    fake = FakeRedis()
    fake.sets[f"seen:cty:{UID}"] = {"US"}  # already-seen baseline
    p1, p2, p3, p4 = _patched(fake, ("DE", "Germany"))
    with p1, p2, p3, p4 as log_activity:
        await signal_service.on_login_success(
            AsyncMock(), user_id=UID, ip="203.0.113.9", user_agent="UA"
        )
    cty = [
        c.kwargs
        for c in log_activity.await_args_list
        if c.kwargs["action"] == "login_new_country"
    ]
    assert len(cty) == 1
    assert cty[0]["detail"]["country"] == "DE"
    assert cty[0]["detail"]["severity"] == "medium"
    assert fake.sets[f"seen:cty:{UID}"] == {"US", "DE"}


@pytest.mark.asyncio
async def test_known_country_is_quiet():
    from src.services import signal_service

    fake = FakeRedis()
    fake.sets[f"seen:cty:{UID}"] = {"US"}
    p1, p2, p3, p4 = _patched(fake, ("US", "United States"))
    with p1, p2, p3, p4 as log_activity:
        await signal_service.on_login_success(
            AsyncMock(), user_id=UID, ip="203.0.113.9", user_agent="UA"
        )
    assert not [
        c
        for c in log_activity.await_args_list
        if c.kwargs["action"] == "login_new_country"
    ]


@pytest.mark.asyncio
async def test_new_device_signal_uses_family_key():
    from src.services import signal_service

    chrome_mac = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
    firefox_win = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0"
    fake = FakeRedis()
    fake.sets[f"seen:dev:{UID}"] = {"Chrome|macOS"}
    p1, p2, p3, p4 = _patched(fake, ("US", "United States"))
    with p1, p2, p3, p4 as log_activity:
        # Same family (Chrome/macOS, newer build) — quiet.
        await signal_service.on_login_success(
            AsyncMock(), user_id=UID, ip="203.0.113.9", user_agent=chrome_mac
        )
        # New family — signal.
        await signal_service.on_login_success(
            AsyncMock(), user_id=UID, ip="203.0.113.9", user_agent=firefox_win
        )
    dev = [
        c.kwargs
        for c in log_activity.await_args_list
        if c.kwargs["action"] == "login_new_device"
    ]
    assert len(dev) == 1
    assert dev[0]["detail"]["device"] == "Firefox|Windows"
    assert dev[0]["detail"]["severity"] == "low"


@pytest.mark.asyncio
async def test_first_login_seeds_both_sets_silently():
    from src.services import signal_service

    fake = FakeRedis()
    p1, p2, p3, p4 = _patched(fake, ("US", "United States"))
    with p1, p2, p3, p4 as log_activity:
        await signal_service.on_login_success(
            AsyncMock(), user_id=UID, ip="203.0.113.9", user_agent="curl/8.0"
        )
    log_activity.assert_not_awaited()
    assert fake.sets[f"seen:cty:{UID}"] == {"US"}
    assert fake.sets[f"seen:dev:{UID}"] == {"curl|Other"}


def _stuffing_settings(monkeypatch):
    monkeypatch.setattr(settings, "signal_stuffing_failures", 3)
    monkeypatch.setattr(settings, "signal_stuffing_distinct_emails", 2)


@pytest.mark.asyncio
async def test_stuffing_fires_on_spread_not_volume(monkeypatch):
    from src.services import signal_service

    _stuffing_settings(monkeypatch)
    fake = FakeRedis()
    with (
        patch.object(signal_service, "get_redis", AsyncMock(return_value=fake)),
        patch(
            "src.services.activity_service.log_activity", new_callable=AsyncMock
        ) as log_activity,
    ):
        # 3 failures, ONE email — volume without spread: quiet.
        for _ in range(3):
            await signal_service.on_login_failure(
                AsyncMock(), ip="198.51.100.7", user_agent="UA", email="a@x.com"
            )
        log_activity.assert_not_awaited()
        # A second distinct email crosses both thresholds: one signal.
        await signal_service.on_login_failure(
            AsyncMock(), ip="198.51.100.7", user_agent="UA", email="b@x.com"
        )
        stuffing = [
            c.kwargs
            for c in log_activity.await_args_list
            if c.kwargs["action"] == "credential_stuffing_suspected"
        ]
        assert len(stuffing) == 1
        d = stuffing[0]["detail"]
        assert d["failures"] == 4 and d["distinct_emails"] == 2
        assert stuffing[0]["target_type"] == "system"
        assert stuffing[0]["actor_id"] is None
        # Further failures in the same window stay quiet (flag marker).
        await signal_service.on_login_failure(
            AsyncMock(), ip="198.51.100.7", user_agent="UA", email="c@x.com"
        )
        assert (
            len(
                [
                    c
                    for c in log_activity.await_args_list
                    if c.kwargs["action"] == "credential_stuffing_suspected"
                ]
            )
            == 1
        )


@pytest.mark.asyncio
async def test_stuffing_counters_are_per_ip(monkeypatch):
    from src.services import signal_service

    _stuffing_settings(monkeypatch)
    fake = FakeRedis()
    with (
        patch.object(signal_service, "get_redis", AsyncMock(return_value=fake)),
        patch(
            "src.services.activity_service.log_activity", new_callable=AsyncMock
        ) as log_activity,
    ):
        for i, ip in enumerate(["1.1.1.1", "2.2.2.2", "3.3.3.3", "4.4.4.4"]):
            await signal_service.on_login_failure(
                AsyncMock(), ip=ip, user_agent="UA", email=f"u{i}@x.com"
            )
    log_activity.assert_not_awaited()


@pytest.mark.asyncio
async def test_login_failure_helper_feeds_counters():
    """_log_login_failure calls on_login_failure by default…"""
    from src.api.auth_routes import _log_login_failure

    db = AsyncMock()
    with (
        patch("src.services.activity_service.log_activity", new_callable=AsyncMock),
        patch(
            "src.api.auth_routes.signal_service.on_login_failure",
            new_callable=AsyncMock,
        ) as on_failure,
    ):
        request = MagicMock()
        request.client.host = "203.0.113.9"
        request.headers = {"user-agent": "TestUA/1.0"}
        await _log_login_failure(
            db, request, provider="google", reason="org_not_permitted", email="e@x.com"
        )
    on_failure.assert_awaited_once()
    kw = on_failure.await_args.kwargs
    assert kw["ip"] == "203.0.113.9" and kw["email"] == "e@x.com"


@pytest.mark.asyncio
async def test_login_failure_helper_can_skip_counters():
    """…and count_for_stuffing=False keeps config-shaped rejects out of them."""
    from src.api.auth_routes import _log_login_failure

    db = AsyncMock()
    with (
        patch("src.services.activity_service.log_activity", new_callable=AsyncMock),
        patch(
            "src.api.auth_routes.signal_service.on_login_failure",
            new_callable=AsyncMock,
        ) as on_failure,
    ):
        request = MagicMock()
        request.client.host = "203.0.113.9"
        request.headers = {"user-agent": "TestUA/1.0"}
        await _log_login_failure(
            db,
            request,
            provider="google",
            reason="redirect_uri_not_allowed",
            count_for_stuffing=False,
        )
    on_failure.assert_not_awaited()


# ---------------------------------------------------------------------------
# rotate_refresh_token -> on_refresh_ip_changed wiring
# ---------------------------------------------------------------------------


def _write_keypair(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    priv_path = tmp_path / "private.pem"
    pub_path = tmp_path / "public.pem"
    priv_path.write_bytes(priv)
    pub_path.write_bytes(pub)
    return priv_path, pub_path


@pytest.fixture
def _ephemeral_keys(tmp_path, monkeypatch):
    """Swap JWT key paths for temp files so this test doesn't depend on keys/."""
    from src.auth import key_provider

    priv_path, pub_path = _write_keypair(tmp_path)
    monkeypatch.setattr(settings, "jwt_private_key_path", priv_path)
    monkeypatch.setattr(settings, "jwt_public_key_path", pub_path)
    key_provider.reset_cache()
    yield
    key_provider.reset_cache()


def _fake_user_for_rotate():
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "test@example.com"
    user.name = "Test User"
    user.is_active = True
    user.is_admin = False
    user.organization_id = None
    return user


def _fake_db_for_rotate(user, workspace_id):
    """Mock DB that lets ``rotate_refresh_token`` progress past its queries."""
    workspace = MagicMock()
    workspace.id = workspace_id
    workspace.slug = "test-ws"

    membership = MagicMock()
    membership.role = "editor"

    db = MagicMock()
    db.get = AsyncMock(side_effect=[user, workspace])

    membership_result = MagicMock()
    membership_result.scalar_one_or_none.return_value = membership

    allows_org_scalars = MagicMock()
    allows_org_scalars.all.return_value = []
    allows_org_result = MagicMock()
    allows_org_result.scalars.return_value = allows_org_scalars

    groups_result = MagicMock()
    groups_result.all.return_value = []
    db.execute = AsyncMock(
        side_effect=[membership_result, allows_org_result, groups_result]
    )
    db.commit = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_rotate_calls_travel_signal_on_context_change(_ephemeral_keys):
    """rotate_refresh_token must hand changed-context refreshes to the signal
    service (travel rule), after the refresh_context_changed row."""
    from src.auth.jwt import create_refresh_token
    from src.services import auth_service

    user = _fake_user_for_rotate()
    workspace_id = uuid.uuid4()
    family_id = str(uuid.uuid4())
    refresh_token = create_refresh_token(user_id=user.id, family_id=family_id)

    db = _fake_db_for_rotate(user, workspace_id)

    async def fake_consume(_jti):
        return (user.id, family_id, workspace_id, None)

    async def fake_store(**_kwargs):
        pass

    with (
        patch(
            "src.services.auth_service.token_service.consume_refresh_token",
            new=fake_consume,
        ),
        patch(
            "src.services.auth_service.token_service.store_refresh_token",
            new=fake_store,
        ),
        patch(
            "src.services.auth_service.token_service.swap_refresh_context",
            new=AsyncMock(return_value={"ip": "1.1.1.1", "ua": "old"}),
        ),
        patch(
            "src.services.auth_service.signal_service.on_refresh_ip_changed",
            new_callable=AsyncMock,
        ) as on_changed,
        patch("src.services.activity_service.log_activity", new_callable=AsyncMock),
    ):
        await auth_service.rotate_refresh_token(
            db, refresh_token, ip="9.9.9.9", user_agent="new-ua"
        )

    on_changed.assert_awaited_once()
    kw = on_changed.await_args.kwargs
    assert kw["user_id"] == user.id and kw["ip"] == "9.9.9.9"


# ---------------------------------------------------------------------------
# Start-endpoint audit gap: login-start rejects route through
# _log_login_failure with stream_event="auth.login.rejected"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_reject_writes_row_with_rejected_stream_event():
    from structlog.testing import capture_logs

    from src.api.auth_routes import _log_login_failure

    db = AsyncMock()
    request = MagicMock()
    request.client.host = "203.0.113.9"
    request.headers = {"user-agent": "TestUA/1.0"}
    with (
        patch(
            "src.services.activity_service.log_activity", new_callable=AsyncMock
        ) as log_activity,
        patch(
            "src.api.auth_routes.signal_service.on_login_failure",
            new_callable=AsyncMock,
        ) as on_failure,
        capture_logs() as logs,
    ):
        await _log_login_failure(
            db,
            request,
            provider="google",
            reason="redirect_uri_not_allowed",
            count_for_stuffing=False,
            stream_event="auth.login.rejected",
            client_id="abc",
            redirect_uri="https://evil.example/cb",
        )
    kw = log_activity.await_args.kwargs
    assert kw["action"] == "login_failed"
    assert kw["detail"]["reason"] == "redirect_uri_not_allowed"
    assert kw["detail"]["redirect_uri"] == "https://evil.example/cb"
    on_failure.assert_not_awaited()
    rejected = [e for e in logs if e.get("event") == "auth.login.rejected"]
    assert rejected and rejected[0]["outcome"] == "denied"
    assert rejected[0]["redirect_uri"] == "https://evil.example/cb"
    assert rejected[0]["source_ip"] == "203.0.113.9"


def test_login_start_rejects_unknown_provider_and_logs():
    """Endpoint-level: GET /auth/login/{bogus} 400s AND writes the audit row."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.middleware.sessions import SessionMiddleware

    from src.api.auth_routes import router
    from src.database import get_db

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")
    from slowapi.errors import RateLimitExceeded

    from src.middleware.rate_limit import limiter, rate_limit_exceeded_handler

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(router)

    async def _db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = _db

    with patch(
        "src.services.activity_service.log_activity", new_callable=AsyncMock
    ) as log_activity:
        client = TestClient(app)
        resp = client.get(
            "/auth/login/bogus",
            params={
                "client_id": str(uuid.uuid4()),
                "redirect_uri": "https://x.example/cb",
                "code_challenge": "c" * 43,
            },
        )
    assert resp.status_code == 400
    kw = log_activity.await_args.kwargs
    assert kw["action"] == "login_failed"
    assert kw["detail"]["reason"] == "provider_not_configured"
