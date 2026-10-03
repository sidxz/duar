"""Tier-1 security signals — detect-only anomaly rules on auth events.

Design: docs/superpowers/specs/2026-07-28-tier1-detection-design.md
Every public entry point is fail-open: a telemetry failure logs a warning and
the auth flow proceeds untouched (same contract as swap_refresh_context).
Detection never sits in the authz decision path.
"""

import contextlib
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
        with contextlib.suppress(Exception):
            await db.rollback()
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
        with contextlib.suppress(Exception):
            await db.rollback()
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
        with contextlib.suppress(Exception):
            await db.rollback()
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


async def _emit(db, *, action, signal, severity, user_id, detail, workspace_id=None):
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
        f"auth.signal.{signal}",
        outcome="anomaly",
        severity=severity,
        level="warning" if severity == "high" else None,
        **stream,
    )
