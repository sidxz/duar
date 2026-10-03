# Tier-1 Security Signals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect-only security signals — impossible travel, new-country/new-device flags, credential-stuffing detection — emitted as activity events from existing auth hot points, plus the start-endpoint audit gap fix and a Dashboard signals card.

**Architecture:** One new service module (`signal_service.py`) called fail-open from three existing hot points (login success ×2, refresh-context change, login failure). State lives in Redis via the existing `get_redis()` singleton. Signals are ordinary `log_activity` rows + `log_security("auth.signal.*")` stream emits. No new endpoints; the admin card reads the existing activity summary.

**Tech Stack:** FastAPI, SQLAlchemy async, Redis (redis-py asyncio), geoip2fast (already a dep, country-only), structlog, React + React Query + Tailwind (admin).

**Spec:** `docs/superpowers/specs/2026-07-28-tier1-detection-design.md`

## Global Constraints

- **Fail-open contract:** every `signal_service` public entry point swallows exceptions after `logger.warning(...)`; a telemetry failure must NEVER break or block an auth flow (same contract as `swap_refresh_context`).
- **No new Python dependencies.** geoip2fast stays on its bundled country-only dataset.
- **Detect-only.** No enforcement anywhere: no revokes, no blocking, no extra 4xx.
- Python 3.12; run `make fmt` before each commit; tests run with `cd service && uv run pytest`.
- Admin frontend: semantic theme tokens only (`text-muted-foreground`, `border-border`, …) — never `zinc-*`; status red = `text-red-600 dark:text-red-400` (both themes).
- New activity actions (exact strings): `login_impossible_travel`, `login_new_country`, `login_new_device`, `credential_stuffing_suspected`.
- Config flags (exact names, defaults): `signals_enabled=True`, `signal_impossible_travel_kmh=900`, `signal_stuffing_window_minutes=15`, `signal_stuffing_failures=10`, `signal_stuffing_distinct_emails=5`.

---

### Task 1: Country centroids + haversine

**Files:**
- Create: `service/src/services/country_centroids.py`
- Test: `service/tests/test_signals.py` (new file, first tests)

**Interfaces:**
- Produces: `haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float`; `centroid_km(cc1: str, cc2: str) -> float | None` (None when either ISO2 code is unknown — callers skip the signal). `CENTROIDS: dict[str, tuple[float, float]]`.

- [ ] **Step 1: Write the failing tests**

Create `service/tests/test_signals.py`:

```python
"""Tier-1 security-signal tests: centroids, travel, first-seen, stuffing."""

from __future__ import annotations

import pytest

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
```

- [ ] **Step 2: Run to verify failure**

Run: `cd service && uv run pytest tests/test_signals.py -x -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.country_centroids'`

- [ ] **Step 3: Implement `service/src/services/country_centroids.py`**

```python
"""Approximate country centroids for impossible-travel distance estimates.

ponytail: static centroid table, not a city-level geo dataset — at a 900 km/h
threshold, ±200 km of centroid error is noise. Misses intra-country jumps
(US coast-to-coast); city-level lat/lon (geoip2fast-city dataset) is the
upgrade path. Codes missing from the table simply never signal.
"""

import math

# ISO 3166-1 alpha-2 → (lat, lon). Approximate geographic centroids.
CENTROIDS: dict[str, tuple[float, float]] = {
    "AD": (42.5, 1.5), "AE": (24.0, 54.0), "AF": (33.0, 65.0), "AG": (17.05, -61.8),
    "AI": (18.25, -63.17), "AL": (41.0, 20.0), "AM": (40.0, 45.0), "AO": (-12.5, 18.5),
    "AR": (-34.0, -64.0), "AS": (-14.33, -170.0), "AT": (47.33, 13.33), "AU": (-27.0, 133.0),
    "AW": (12.5, -69.97), "AZ": (40.5, 47.5), "BA": (44.0, 18.0), "BB": (13.17, -59.53),
    "BD": (24.0, 90.0), "BE": (50.83, 4.0), "BF": (13.0, -2.0), "BG": (43.0, 25.0),
    "BH": (26.0, 50.55), "BI": (-3.5, 30.0), "BJ": (9.5, 2.25), "BM": (32.33, -64.75),
    "BN": (4.5, 114.67), "BO": (-17.0, -65.0), "BR": (-10.0, -55.0), "BS": (24.25, -76.0),
    "BT": (27.5, 90.5), "BW": (-22.0, 24.0), "BY": (53.0, 28.0), "BZ": (17.25, -88.75),
    "CA": (60.0, -95.0), "CD": (0.0, 25.0), "CF": (7.0, 21.0), "CG": (-1.0, 15.0),
    "CH": (47.0, 8.0), "CI": (8.0, -5.0), "CL": (-30.0, -71.0), "CM": (6.0, 12.0),
    "CN": (35.0, 105.0), "CO": (4.0, -72.0), "CR": (10.0, -84.0), "CU": (21.5, -80.0),
    "CV": (16.0, -24.0), "CY": (35.0, 33.0), "CZ": (49.75, 15.5), "DE": (51.0, 9.0),
    "DJ": (11.5, 43.0), "DK": (56.0, 10.0), "DM": (15.42, -61.33), "DO": (19.0, -70.67),
    "DZ": (28.0, 3.0), "EC": (-2.0, -77.5), "EE": (59.0, 26.0), "EG": (27.0, 30.0),
    "ER": (15.0, 39.0), "ES": (40.0, -4.0), "ET": (8.0, 38.0), "FI": (64.0, 26.0),
    "FJ": (-18.0, 175.0), "FM": (6.92, 158.25), "FO": (62.0, -7.0), "FR": (46.0, 2.0),
    "GA": (-1.0, 11.75), "GB": (54.0, -2.0), "GD": (12.12, -61.67), "GE": (42.0, 43.5),
    "GF": (4.0, -53.0), "GH": (8.0, -2.0), "GI": (36.13, -5.35), "GL": (72.0, -40.0),
    "GM": (13.47, -16.57), "GN": (11.0, -10.0), "GP": (16.25, -61.58), "GQ": (2.0, 10.0),
    "GR": (39.0, 22.0), "GT": (15.5, -90.25), "GU": (13.47, 144.78), "GW": (12.0, -15.0),
    "GY": (5.0, -59.0), "HK": (22.25, 114.17), "HN": (15.0, -86.5), "HR": (45.17, 15.5),
    "HT": (19.0, -72.42), "HU": (47.0, 20.0), "ID": (-5.0, 120.0), "IE": (53.0, -8.0),
    "IL": (31.5, 34.75), "IN": (20.0, 77.0), "IQ": (33.0, 44.0), "IR": (32.0, 53.0),
    "IS": (65.0, -18.0), "IT": (42.83, 12.83), "JM": (18.25, -77.5), "JO": (31.0, 36.0),
    "JP": (36.0, 138.0), "KE": (1.0, 38.0), "KG": (41.0, 75.0), "KH": (13.0, 105.0),
    "KI": (1.42, 173.0), "KM": (-12.17, 44.25), "KN": (17.33, -62.75), "KP": (40.0, 127.0),
    "KR": (37.0, 127.5), "KW": (29.34, 47.66), "KY": (19.5, -80.5), "KZ": (48.0, 68.0),
    "LA": (18.0, 105.0), "LB": (33.83, 35.83), "LC": (13.88, -61.13), "LI": (47.17, 9.53),
    "LK": (7.0, 81.0), "LR": (6.5, -9.5), "LS": (-29.5, 28.5), "LT": (56.0, 24.0),
    "LU": (49.75, 6.17), "LV": (57.0, 25.0), "LY": (25.0, 17.0), "MA": (32.0, -5.0),
    "MC": (43.73, 7.4), "MD": (47.0, 29.0), "ME": (42.5, 19.3), "MG": (-20.0, 47.0),
    "MH": (9.0, 168.0), "MK": (41.83, 22.0), "ML": (17.0, -4.0), "MM": (22.0, 98.0),
    "MN": (46.0, 105.0), "MO": (22.17, 113.55), "MQ": (14.67, -61.0), "MR": (20.0, -12.0),
    "MT": (35.83, 14.58), "MU": (-20.28, 57.55), "MV": (3.25, 73.0), "MW": (-13.5, 34.0),
    "MX": (23.0, -102.0), "MY": (2.5, 112.5), "MZ": (-18.25, 35.0), "NA": (-22.0, 17.0),
    "NC": (-21.5, 165.5), "NE": (16.0, 8.0), "NG": (10.0, 8.0), "NI": (13.0, -85.0),
    "NL": (52.5, 5.75), "NO": (62.0, 10.0), "NP": (28.0, 84.0), "NR": (-0.53, 166.92),
    "NZ": (-41.0, 174.0), "OM": (21.0, 57.0), "PA": (9.0, -80.0), "PE": (-10.0, -76.0),
    "PF": (-15.0, -140.0), "PG": (-6.0, 147.0), "PH": (13.0, 122.0), "PK": (30.0, 70.0),
    "PL": (52.0, 20.0), "PR": (18.25, -66.5), "PS": (32.0, 35.25), "PT": (39.5, -8.0),
    "PW": (7.5, 134.5), "PY": (-23.0, -58.0), "QA": (25.5, 51.25), "RE": (-21.1, 55.6),
    "RO": (46.0, 25.0), "RS": (44.0, 21.0), "RU": (60.0, 100.0), "RW": (-2.0, 30.0),
    "SA": (25.0, 45.0), "SB": (-8.0, 159.0), "SC": (-4.58, 55.67), "SD": (15.0, 30.0),
    "SE": (62.0, 15.0), "SG": (1.37, 103.8), "SI": (46.12, 14.82), "SK": (48.67, 19.5),
    "SL": (8.5, -11.5), "SM": (43.77, 12.42), "SN": (14.0, -14.0), "SO": (10.0, 49.0),
    "SR": (4.0, -56.0), "SS": (8.0, 30.0), "ST": (1.0, 7.0), "SV": (13.83, -88.92),
    "SY": (35.0, 38.0), "SZ": (-26.5, 31.5), "TD": (15.0, 19.0), "TG": (8.0, 1.17),
    "TH": (15.0, 100.0), "TJ": (39.0, 71.0), "TL": (-8.83, 125.92), "TM": (40.0, 60.0),
    "TN": (34.0, 9.0), "TO": (-20.0, -175.0), "TR": (39.0, 35.0), "TT": (11.0, -61.0),
    "TW": (23.5, 121.0), "TZ": (-6.0, 35.0), "UA": (49.0, 32.0), "UG": (1.0, 32.0),
    "US": (38.0, -97.0), "UY": (-33.0, -56.0), "UZ": (41.0, 64.0), "VC": (13.25, -61.2),
    "VE": (8.0, -66.0), "VG": (18.42, -64.62), "VI": (18.34, -64.93), "VN": (16.0, 106.0),
    "VU": (-16.0, 167.0), "WS": (-13.58, -172.33), "XK": (42.58, 21.0), "YE": (15.0, 48.0),
    "ZA": (-29.0, 24.0), "ZM": (-15.0, 30.0), "ZW": (-20.0, 30.0),
}

_EARTH_RADIUS_KM = 6371.0


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance between two (lat, lon) points in km."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def centroid_km(cc1: str, cc2: str) -> float | None:
    """Centroid distance between two ISO2 country codes; None if either unknown."""
    a, b = CENTROIDS.get(cc1), CENTROIDS.get(cc2)
    if a is None or b is None:
        return None
    return haversine_km(a, b)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd service && uv run pytest tests/test_signals.py -x -q`
Expected: 5 passed

- [ ] **Step 5: Format + commit**

```bash
make fmt
git add service/src/services/country_centroids.py service/tests/test_signals.py
git commit -m "feat(service): country-centroid table + haversine for travel signals"
```

---

### Task 2: signal_service skeleton + impossible-travel rule + config flags

**Files:**
- Create: `service/src/services/signal_service.py`
- Modify: `service/src/config.py` (add flags after the rate-limit block, ~line 123)
- Test: `service/tests/test_signals.py` (append)

**Interfaces:**
- Consumes: `centroid_km` (Task 1), `insights_service._lookup_country` / `parse_user_agent`, `token_service.get_redis`, `activity_service.log_activity`, `log_security`.
- Produces (later tasks wire these):
  - `async def on_login_success(db, *, user_id: uuid.UUID, ip: str, user_agent: str, workspace_id=None) -> None`
  - `async def on_refresh_ip_changed(db, *, user_id: uuid.UUID, workspace_id, ip: str, user_agent: str) -> None`
  - internal `_emit(db, *, action, signal, severity, user_id, detail, workspace_id=None)` and `_now()` (tests monkeypatch `signal_service._now`).

- [ ] **Step 1: Add config flags to `service/src/config.py`**

Insert after the rate-limit validator block (after line 122, before `# Admin`):

```python
    # Tier-1 security signals (detect-only; see docs/ai-security-roadmap.md)
    signals_enabled: bool = True
    signal_impossible_travel_kmh: int = 900  # implied speed above this → signal
    signal_stuffing_window_minutes: int = 15  # sliding window for failure counters
    signal_stuffing_failures: int = 10  # failures per IP in window, AND:
    signal_stuffing_distinct_emails: int = 5  # distinct emails per IP in window
```

- [ ] **Step 2: Write the failing tests**

Append to `service/tests/test_signals.py`:

```python
import json
import uuid
from unittest.mock import AsyncMock, patch

from src.config import settings


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
        c for c in log_activity.await_args_list
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
        c for c in log_activity.await_args_list
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
        c for c in log_activity.await_args_list
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
        c for c in log_activity.await_args_list
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
```

- [ ] **Step 3: Run to verify failure**

Run: `cd service && uv run pytest tests/test_signals.py -x -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.signal_service'`

- [ ] **Step 4: Implement `service/src/services/signal_service.py`**

```python
"""Tier-1 security signals — detect-only anomaly rules on auth events.

Design: docs/superpowers/specs/2026-07-28-tier1-detection-design.md
Every public entry point is fail-open: a telemetry failure logs a warning and
the auth flow proceeds untouched (same contract as swap_refresh_context).
Detection never sits in the authz decision path.
"""

import json
import time
import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.logging_events import log_security
from src.services import activity_service
from src.services.country_centroids import centroid_km
from src.services.insights_service import _lookup_country, parse_user_agent
from src.services.token_service import get_redis

logger = structlog.get_logger()

_now = time.time  # seam for tests

_GEO_LAST = "geo:last:{}"  # user_id → JSON {country, ts}
_TRAVEL_FLAG = "geo:flag:{}:{}:{}"  # user_id, cc_a, cc_b (sorted) → damper
_SEEN_CTY = "seen:cty:{}"  # user_id → SET of ISO2 codes
_SEEN_DEV = "seen:dev:{}"  # user_id → SET of "browser|os"
_FAIL_N = "fail:ip:{}"  # ip → failure count in window
_FAIL_EM = "fail:em:{}"  # ip → SET of attempted emails
_FAIL_FLAG = "fail:flag:{}"  # ip → signalled-this-window marker

_GEO_TTL = 90 * 86400
_SEEN_TTL = 365 * 86400
_PAIR_TTL = 6 * 3600


async def on_login_success(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    ip: str,
    user_agent: str,
    workspace_id: uuid.UUID | None = None,
) -> None:
    """Run travel + first-seen rules after a successful user/admin login."""
    if not settings.signals_enabled:
        return
    try:
        r = await get_redis()
        geo = _lookup_country(ip)
        country = geo[0] if geo else None
        if country:
            await _check_travel(db, r, user_id, country, ip, user_agent, workspace_id)
            await _check_first_seen(
                db,
                r,
                user_id=user_id,
                key=_SEEN_CTY.format(user_id),
                member=country,
                action="login_new_country",
                signal="new_country",
                severity="medium",
                workspace_id=workspace_id,
                detail={"ip": ip, "user_agent": user_agent, "country": country},
            )
        device = "|".join(parse_user_agent(user_agent))
        await _check_first_seen(
            db,
            r,
            user_id=user_id,
            key=_SEEN_DEV.format(user_id),
            member=device,
            action="login_new_device",
            signal="new_device",
            severity="low",
            workspace_id=workspace_id,
            detail={
                "ip": ip,
                "user_agent": user_agent,
                "device": device,
                "country": country,
            },
        )
    except Exception:
        logger.warning(
            "signal.evaluate_failed",
            category="app",
            reason="login_success",
            exc_info=True,
        )


async def on_refresh_ip_changed(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
    ip: str,
    user_agent: str,
) -> None:
    """Travel rule only — runs when a refresh family's ip/UA context changed."""
    if not settings.signals_enabled:
        return
    try:
        geo = _lookup_country(ip)
        if not geo:
            return
        r = await get_redis()
        await _check_travel(db, r, user_id, geo[0], ip, user_agent, workspace_id)
    except Exception:
        logger.warning(
            "signal.evaluate_failed",
            category="app",
            reason="refresh_context",
            exc_info=True,
        )


async def on_login_failure(
    db: AsyncSession, *, ip: str, user_agent: str, email: str | None
) -> None:
    """Credential-stuffing counters — called for credential-shaped failures only."""
    if not settings.signals_enabled:
        return
    try:
        r = await get_redis()
        window = settings.signal_stuffing_window_minutes * 60
        n = await r.incr(_FAIL_N.format(ip))
        if n == 1:
            await r.expire(_FAIL_N.format(ip), window)
        if email:
            await r.sadd(_FAIL_EM.format(ip), email.lower())
            await r.expire(_FAIL_EM.format(ip), window)
        distinct = await r.scard(_FAIL_EM.format(ip))
        if (
            n < settings.signal_stuffing_failures
            or distinct < settings.signal_stuffing_distinct_emails
        ):
            return
        if not await r.set(_FAIL_FLAG.format(ip), "1", ex=window, nx=True):
            return  # already signalled this window
        await _emit(
            db,
            action="credential_stuffing_suspected",
            signal="credential_stuffing",
            severity="high",
            user_id=None,
            detail={
                "ip": ip,
                "user_agent": user_agent,
                "failures": n,
                "distinct_emails": distinct,
                "window_minutes": settings.signal_stuffing_window_minutes,
            },
        )
    except Exception:
        logger.warning(
            "signal.evaluate_failed",
            category="app",
            reason="login_failure",
            exc_info=True,
        )


async def _check_travel(db, r, user_id, country, ip, user_agent, workspace_id):
    key = _GEO_LAST.format(user_id)
    now = _now()
    raw = await r.get(key)
    await r.set(key, json.dumps({"country": country, "ts": now}), ex=_GEO_TTL)
    if not raw:
        return
    prev = json.loads(raw)
    if prev["country"] == country:
        return
    km = centroid_km(prev["country"], country)
    if km is None:
        return
    # Clamp Δt to ≥60s: two countries inside a minute IS the signal, not a /0.
    hours = max(now - prev["ts"], 60) / 3600
    kmh = km / hours
    if kmh <= settings.signal_impossible_travel_kmh:
        return
    a, b = sorted((prev["country"], country))
    if not await r.set(_TRAVEL_FLAG.format(user_id, a, b), "1", ex=_PAIR_TTL, nx=True):
        return  # this country pair already signalled recently (VPN ping-pong)
    await _emit(
        db,
        action="login_impossible_travel",
        signal="impossible_travel",
        severity="high",
        user_id=user_id,
        workspace_id=workspace_id,
        detail={
            "ip": ip,
            "user_agent": user_agent,
            "prev_country": prev["country"],
            "country": country,
            "km": round(km),
            "minutes": round((now - prev["ts"]) / 60),
            "kmh": round(kmh),
        },
    )


async def _check_first_seen(
    db, r, *, user_id, key, member, action, signal, severity, workspace_id, detail
):
    empty = (await r.scard(key)) == 0
    added = await r.sadd(key, member)
    await r.expire(key, _SEEN_TTL)
    if empty or not added:
        return  # first login ever seeds silently; known member is quiet
    await _emit(
        db,
        action=action,
        signal=signal,
        severity=severity,
        user_id=user_id,
        workspace_id=workspace_id,
        detail=detail,
    )


async def _emit(
    db, *, action, signal, severity, user_id, detail, workspace_id=None
):
    """Activity row + auth.signal.* stream event. The consistent envelope
    (signal/severity + rule fields) is the Tier-2 risk-scorer seam."""
    row_detail = {"signal": signal, "severity": severity, **detail}
    await activity_service.log_activity(
        db,
        action=action,
        target_type="user" if user_id else "system",
        target_id=user_id if user_id else uuid.UUID(int=0),
        actor_id=user_id,
        workspace_id=workspace_id,
        detail=row_detail,
    )
    await db.commit()
    stream = {k: v for k, v in detail.items() if k != "user_agent"}
    stream["source_ip"] = stream.pop("ip", None)
    if user_id:
        stream["actor"] = str(user_id)
    log_security(
        f"auth.signal.{signal}", outcome="anomaly", severity=severity, **stream
    )
```

- [ ] **Step 5: Run to verify pass**

Run: `cd service && uv run pytest tests/test_signals.py -x -q`
Expected: 12 passed

- [ ] **Step 6: Format + commit**

```bash
make fmt
git add service/src/services/signal_service.py service/src/config.py service/tests/test_signals.py
git commit -m "feat(service): signal service — impossible-travel rule + config flags"
```

---

### Task 3: First-seen rules — new country + new device

The mechanics already exist (`_check_first_seen`, wired inside `on_login_success` in Task 2). This task pins their behavior with tests.

**Files:**
- Test: `service/tests/test_signals.py` (append)

**Interfaces:**
- Consumes: `on_login_success`, `FakeRedis`, `_patched` (Task 2).

- [ ] **Step 1: Write the tests**

```python
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
        c.kwargs for c in log_activity.await_args_list
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
        c for c in log_activity.await_args_list
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
        c.kwargs for c in log_activity.await_args_list
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
```

- [ ] **Step 2: Run — expect pass** (implementation shipped in Task 2)

Run: `cd service && uv run pytest tests/test_signals.py -x -q`
Expected: 16 passed. If any fail, fix `signal_service.py` — the tests are the contract.

- [ ] **Step 3: Commit**

```bash
make fmt
git add service/tests/test_signals.py
git commit -m "test(service): pin first-seen country/device signal semantics"
```

---

### Task 4: Credential-stuffing rule wiring into `_log_login_failure`

`on_login_failure` exists (Task 2). This task adds the `count_for_stuffing` param to `_log_login_failure`, calls the rule from it, and pins behavior.

**Files:**
- Modify: `service/src/api/auth_routes.py:95-146` (`_log_login_failure`)
- Test: `service/tests/test_signals.py` (append)

**Interfaces:**
- Consumes: `signal_service.on_login_failure` (Task 2).
- Produces: `_log_login_failure(..., count_for_stuffing: bool = True)` — Task 6 passes `False` from start endpoints.

- [ ] **Step 1: Write the failing tests**

```python
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
            c.kwargs for c in log_activity.await_args_list
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
            len([
                c for c in log_activity.await_args_list
                if c.kwargs["action"] == "credential_stuffing_suspected"
            ])
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
```

Also add `from unittest.mock import MagicMock` to the test-file imports.

- [ ] **Step 2: Run to verify failure**

Run: `cd service && uv run pytest tests/test_signals.py -x -q`
Expected: `test_login_failure_helper_feeds_counters` FAILS (`signal_service` not imported / param missing). The two pure-rule tests pass already.

- [ ] **Step 3: Wire `_log_login_failure`**

In `service/src/api/auth_routes.py`:

1. Add to imports (near the other service imports): `from src.services import signal_service` (keep the existing `activity_service` import line as-is).
2. Change the signature (line 95):

```python
async def _log_login_failure(
    db: AsyncSession,
    request: Request,
    provider: str,
    reason: str,
    flow: str = "user",
    email: str | None = None,
    error_type: str | None = None,
    count_for_stuffing: bool = True,
) -> None:
```

3. After `await db.commit()` (line 139), still inside the `try`, add:

```python
        if count_for_stuffing:
            await signal_service.on_login_failure(
                db,
                ip=detail["ip"],
                user_agent=detail["user_agent"],
                email=email,
            )
```

(`on_login_failure` is itself fail-open; the outer `except` is belt-and-braces.)

- [ ] **Step 4: Run to verify pass — including the pre-existing failure-audit suite**

Run: `cd service && uv run pytest tests/test_signals.py tests/test_login_failure_audit.py -q`
Expected: all pass (the old suite proves the helper's rollback→commit contract is untouched).

- [ ] **Step 5: Format + commit**

```bash
make fmt
git add service/src/api/auth_routes.py service/tests/test_signals.py
git commit -m "feat(service): credential-stuffing counters on login failures"
```

---

### Task 5: Wire login-success and refresh call sites

**Files:**
- Modify: `service/src/api/auth_routes.py` (user callback ~line 335, admin callback ~line 796 — right after each `await db.commit()` that follows the login activity row)
- Modify: `service/src/services/auth_service.py` (~line 428, inside the `if prev is not None:` branch of the refresh-context block)
- Test: `service/tests/test_signals.py` (append)

**Interfaces:**
- Consumes: `on_login_success`, `on_refresh_ip_changed` (Task 2).

- [ ] **Step 1: Write the failing test for the refresh path**

Model on `test_context_change_writes_activity_row` in `tests/test_auth_event_logging.py:236-269` — same `_fake_db_for_rotate`/`_fake_user` fixtures are needed; copy the minimal versions into `test_signals.py` rather than importing across test modules:

```python
@pytest.mark.asyncio
async def test_rotate_calls_travel_signal_on_context_change():
    """rotate_refresh_token must hand changed-context refreshes to the signal
    service (travel rule), after the refresh_context_changed row."""
    from src.auth.jwt import create_refresh_token
    from src.models.user import User
    from src.services import auth_service

    user = User(
        id=uuid.uuid4(), email="u@x.com", name="U", is_active=True, is_admin=False
    )
    workspace_id = uuid.uuid4()
    family_id = str(uuid.uuid4())
    refresh_token = create_refresh_token(user_id=user.id, family_id=family_id)

    db = AsyncMock()
    db.get = AsyncMock(return_value=user)
    db.commit = AsyncMock()

    async def fake_consume(_jti):
        return (user.id, family_id, workspace_id, None)

    with (
        patch(
            "src.services.auth_service.token_service.consume_refresh_token",
            new=fake_consume,
        ),
        patch(
            "src.services.auth_service.token_service.store_refresh_token",
            new=AsyncMock(),
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
```

NOTE: `rotate_refresh_token`'s real signature/fixture needs may differ in detail
(workspace resolution, org gate). If this direct approach fights the fixtures,
copy `_fake_db_for_rotate` wholesale from `test_auth_event_logging.py` — the
assertion that matters is `on_changed.assert_awaited_once()` after a non-None
`swap_refresh_context`.

- [ ] **Step 2: Run to verify failure**

Run: `cd service && uv run pytest tests/test_signals.py -x -q -k rotate`
Expected: FAIL — `auth_service` has no attribute `signal_service`.

- [ ] **Step 3: Wire the three call sites**

`service/src/services/auth_service.py`:
1. Line 19: extend the service import to `from src.services import activity_service, organization_service, signal_service, token_service`.
2. Inside the refresh-context `try`, after the `await db.commit()` at line 428 (still inside `if prev is not None:`):

```python
                await signal_service.on_refresh_ip_changed(
                    db,
                    user_id=user.id,
                    workspace_id=workspace_id,
                    ip=ip or "",
                    user_agent=(user_agent or "")[:200],
                )
```

`service/src/api/auth_routes.py` — after each login-success commit:

User callback (after `await db.commit()` at line 335):

```python
        await signal_service.on_login_success(
            db,
            user_id=user.id,
            ip=get_client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:200],
        )
```

Admin callback (after `await db.commit()` at line 796, before the `log_security("auth.login.succeeded", ...)` emit): same four lines verbatim.

- [ ] **Step 4: Run to verify pass**

Run: `cd service && uv run pytest tests/test_signals.py tests/test_auth_event_logging.py -q`
Expected: all pass (the auth-event suite proves rotate's existing contract holds).

Deliberate skip: no OAuth-mock harness exists for the two login callbacks; their wiring is four identical lines placed after the commit, covered by the full-suite run and the release browser check. Do not build an authlib mock stack for this.

- [ ] **Step 5: Format + commit**

```bash
make fmt
git add service/src/api/auth_routes.py service/src/services/auth_service.py service/tests/test_signals.py
git commit -m "feat(service): run security signals on login success + refresh context change"
```

---

### Task 6: Start-endpoint audit gap

**Files:**
- Modify: `service/src/api/auth_routes.py` — `_log_login_failure` (stream-event override), `login` (~lines 164-209), `admin_login` (~lines 635-645)
- Test: `service/tests/test_signals.py` (append)

**Interfaces:**
- Consumes: `_log_login_failure(..., count_for_stuffing=False)` (Task 4).
- Produces: `_log_login_failure(..., stream_event: str = "auth.login.failed", **stream_extra)` — start sites emit `auth.login.rejected` (outcome `denied`) instead, with `client_id`/`redirect_uri` extras flowing to both the stream and the DB detail.

- [ ] **Step 1: Write the failing tests**

```python
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
```

NOTE: mirror the router prefix/import details from `tests/test_authz_github_callback_state.py:37-78` if the minimal app needs adjusting (e.g. router import name, `get_db` module path).

- [ ] **Step 2: Run to verify failure**

Run: `cd service && uv run pytest tests/test_signals.py -x -q -k "start or reject"`
Expected: FAIL — unexpected keyword `stream_event`.

- [ ] **Step 3: Implement**

1. `_log_login_failure` — extend signature and stream emit:

```python
async def _log_login_failure(
    db: AsyncSession,
    request: Request,
    provider: str,
    reason: str,
    flow: str = "user",
    email: str | None = None,
    error_type: str | None = None,
    count_for_stuffing: bool = True,
    stream_event: str = "auth.login.failed",
    **stream_extra: str,
) -> None:
```

Replace the stream-fields block (lines 114-119) with:

```python
    stream_fields: dict = {"provider": provider, "flow": flow, **stream_extra}
    if email and "@" in email:
        stream_fields["email_domain"] = email.split("@", 1)[-1]
    if error_type:
        stream_fields["error_type"] = error_type
    outcome = "denied" if stream_event == "auth.login.rejected" else "failure"
    log_security(stream_event, outcome=outcome, reason=reason, **stream_fields)
```

And fold the extras into the DB detail (after line 127's detail dict literal):

```python
        detail.update(stream_extra)
```

2. `login` start endpoint — the three reject sites:

Unknown provider (before the `_error_page` return at line 168):

```python
        await _log_login_failure(
            db, request, provider, "provider_not_configured",
            count_for_stuffing=False, stream_event="auth.login.rejected",
        )
```

Non-S256 challenge (before the return at line 175): same call with reason `"pkce_method_rejected"`.

Unregistered redirect_uri (lines 193-209): **replace** the standalone `log_security("auth.login.rejected", ...)` block with:

```python
        await _log_login_failure(
            db, request, provider, "redirect_uri_not_allowed",
            count_for_stuffing=False, stream_event="auth.login.rejected",
            client_id=str(client_id), redirect_uri=redirect_uri,
        )
```

(The stream event it emits is identical in name, outcome, and fields to the one removed — no stream-vocabulary change, no double emit.)

3. `admin_login` (line 635-645): add the db dependency and log before raising:

```python
@router.get("/admin/login/{provider}")
@limiter.limit(settings.rate_limit_auth_admin)
async def admin_login(
    provider: str, request: Request, db: AsyncSession = Depends(get_db)
):
    configured = get_configured_providers()
    if provider not in configured:
        await _log_login_failure(
            db, request, provider, "provider_not_configured", flow="admin",
            count_for_stuffing=False, stream_event="auth.login.rejected",
        )
        raise HTTPException(
            status_code=400, detail=f"Provider '{provider}' is not configured"
        )
    ...  # rest unchanged
```

- [ ] **Step 4: Run to verify pass**

Run: `cd service && uv run pytest tests/test_signals.py tests/test_login_failure_audit.py -q`
Expected: all pass.

- [ ] **Step 5: Format + commit**

```bash
make fmt
git add service/src/api/auth_routes.py service/tests/test_signals.py
git commit -m "fix(service): audit login START rejects — close the pre-callback gap"
```

---

### Task 7: Admin frontend — dropdown, chart group, deep-link filter, signals card

**Files:**
- Modify: `admin/src/pages/Activity.tsx` (action list ~line 49; state init ~line 10)
- Modify: `admin/src/components/charts.tsx` (MIX_BUCKETS ~line 205)
- Modify: `admin/src/pages/Dashboard.tsx` (add card between stat cards and charts)

No test framework exists for the admin SPA; the gate is `npm run build` (tsc + vite) passing.

- [ ] **Step 1: Activity dropdown + URL-param filter**

In `admin/src/pages/Activity.tsx`, add the four actions after the `"token_family_revoked", "action_denied",` line:

```ts
    "login_impossible_travel", "login_new_country", "login_new_device",
    "credential_stuffing_suspected",
```

Initialize the action filter from the URL (so the Dashboard card can deep-link). Add `useSearchParams` to the existing react-router-dom import, then change line 10:

```ts
  const [searchParams] = useSearchParams();
  const [action, setAction] = useState(searchParams.get("action") ?? "");
```

- [ ] **Step 2: Chart bucket**

In `admin/src/components/charts.tsx` line ~205, extend the anomalies regex:

```ts
  ["Auth anomalies", /^(login_failed|admin_login_failed|refresh_context_changed|refresh_reuse_detected|tokens_revoked|login_impossible_travel|login_new_country|login_new_device|credential_stuffing_suspected)$/],
```

- [ ] **Step 3: SecuritySignalsCard**

In `admin/src/pages/Dashboard.tsx`, add below the existing `ActivityEntry` helper (and render it between the stat-card grid and the “Security charts” grid):

```tsx
const SIGNAL_TILES = [
  { action: "login_impossible_travel", label: "Impossible travel", high: true },
  { action: "credential_stuffing_suspected", label: "Credential stuffing", high: true },
  { action: "login_new_country", label: "New countries", high: false },
  { action: "login_new_device", label: "New devices", high: false },
];

function SecuritySignalsCard({ items, days = 30 }: { items: import("../types/api").ActivityDailyCount[]; days?: number }) {
  const counts = new Map<string, number>();
  for (const it of items) counts.set(it.action, (counts.get(it.action) ?? 0) + it.count);
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-baseline justify-between mb-3">
        <h2 className="text-sm font-medium text-muted-foreground">Security signals</h2>
        <span className="text-xs text-muted-foreground">Last {days} days</span>
      </div>
      <div className="grid grid-cols-4 gap-3">
        {SIGNAL_TILES.map((t) => {
          const n = counts.get(t.action) ?? 0;
          return (
            <Link
              key={t.action}
              to={`/activity?action=${t.action}`}
              className="rounded-md border border-border p-3 transition-colors hover:bg-muted/50"
            >
              <div
                className={`text-2xl font-bold tabular-nums ${
                  n === 0
                    ? "text-muted-foreground"
                    : t.high
                      ? "text-red-600 dark:text-red-400"
                      : ""
                }`}
              >
                {n}
              </div>
              <div className="text-xs text-muted-foreground mt-1">{t.label}</div>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
```

Render inside `Dashboard`'s JSX, between the stat-card grid (ends line 50) and the security-charts grid (line 53):

```tsx
      {summary ? (
        <SecuritySignalsCard items={summary.items} days={summary.days} />
      ) : (
        <div className="h-28 bg-muted/50 rounded-lg animate-pulse" />
      )}
```

- [ ] **Step 4: Build check**

Run: `cd admin && npm run build`
Expected: tsc + vite complete with no errors.

- [ ] **Step 5: Commit**

```bash
git add admin/src/pages/Activity.tsx admin/src/pages/Dashboard.tsx admin/src/components/charts.tsx
git commit -m "feat(admin): security-signals dashboard card + activity deep links"
```

---

### Task 8: Full verification

- [ ] **Step 1: Full service suite**

Run: `cd service && uv run pytest -q`
Expected: entire suite green (452+ tests before this work; now more).

- [ ] **Step 2: Lint**

Run: `make lint`
Expected: clean. If not: `make fmt`, re-run, amend the offending commit or add a `style:` commit.

- [ ] **Step 3: Live smoke (optional but recommended)**

`make start` + `make admin`; sign in on localhost; confirm the Dashboard shows the Security signals card (zeros are fine), and one `login_new_device`/`login_new_country` row appears in Activity after clearing the Redis seen-keys for your user (`redis-cli --scan --pattern 'seen:*'` → DEL) and re-logging.

- [ ] **Step 4: Final commit if anything moved**

```bash
git status  # confirm clean or commit stragglers
```
