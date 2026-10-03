"""Sign-in insights: device + geo aggregates derived from activity detail.

All derivation is passive — parsed from the user_agent and ip the auth
callbacks already capture at the trust boundary. Duar never runs
collection code inside client apps.
"""

import re
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.activity import ActivityLog

_LOGIN_ACTIONS = ("user_login", "admin_login")

# ponytail: regex family tables, not a UA-parser dependency — coarse families
# are all the charts need; add a real parser only when these run out.
# Order matters: Edge/Opera before Chrome, Chrome before Safari, iOS before
# macOS (iPad UAs contain "like Mac OS X"), Android before Linux.
_BROWSERS = [
    ("Edge", re.compile(r"\bEdg(?:e|A|iOS)?/")),
    ("Opera", re.compile(r"\bOPR/|\bOpera")),
    ("Samsung Internet", re.compile(r"\bSamsungBrowser/")),
    ("Firefox", re.compile(r"\bFirefox/|\bFxiOS/")),
    ("Chrome", re.compile(r"\bChrome/|\bCriOS/")),
    ("Safari", re.compile(r"\bSafari/")),
    ("curl", re.compile(r"^curl/")),
    ("Python client", re.compile(r"python-requests|python-httpx|aiohttp")),
    ("Bot", re.compile(r"bot|crawler|spider", re.IGNORECASE)),
]
_OSES = [
    ("Windows", re.compile(r"Windows NT")),
    ("iOS", re.compile(r"iPhone|iPad|iPod")),
    ("Android", re.compile(r"Android")),
    ("macOS", re.compile(r"Mac OS X|Macintosh")),
    ("ChromeOS", re.compile(r"\bCrOS\b")),
    ("Linux", re.compile(r"Linux|X11")),
]


def parse_user_agent(ua: str) -> tuple[str, str]:
    """Coarse (browser, os) families from a user-agent string."""
    if not ua:
        return "Unknown", "Unknown"
    browser = next((name for name, rx in _BROWSERS if rx.search(ua)), "Other")
    os_name = next((name for name, rx in _OSES if rx.search(ua)), "Other")
    return browser, os_name


_geoip = None


def _lookup_country(ip: str) -> tuple[str, str] | None:
    """ip → (ISO alpha-2, name), or None for private/unresolvable addresses."""
    global _geoip
    if _geoip is None:
        from geoip2fast import GeoIP2Fast

        _geoip = GeoIP2Fast()
    try:
        r = _geoip.lookup(ip)
    except Exception:
        return None
    code = r.country_code
    if not code or code.startswith("-"):
        return None
    return code, r.country_name


async def signin_insights(
    db: AsyncSession, days: int = 30, actor_id: uuid.UUID | None = None
) -> dict:
    """Browser/OS/country counts over sign-in events, optionally for one user.

    Groups by distinct (ip, ua) in SQL — low cardinality — then parses and
    geolocates each distinct pair once in Python, so it works retroactively on
    every stored row with no schema change.
    """
    cutoff = datetime.now(UTC) - timedelta(days=days)
    ip_expr = ActivityLog.detail["ip"].astext.label("ip")
    ua_expr = ActivityLog.detail["user_agent"].astext.label("ua")
    stmt = (
        select(ip_expr, ua_expr, func.count().label("n"))
        .where(
            ActivityLog.action.in_(_LOGIN_ACTIONS),
            ActivityLog.created_at >= cutoff,
        )
        .group_by(ip_expr, ua_expr)
    )
    if actor_id:
        stmt = stmt.where(ActivityLog.actor_id == actor_id)
    rows = (await db.execute(stmt)).all()

    browsers: Counter[str] = Counter()
    oses: Counter[str] = Counter()
    countries: Counter[tuple[str, str]] = Counter()
    unresolved = 0
    total = 0
    for ip, ua, n in rows:
        total += n
        browser, os_name = parse_user_agent(ua or "")
        browsers[browser] += n
        oses[os_name] += n
        geo = _lookup_country(ip) if ip else None
        if geo:
            countries[geo] += n
        else:
            unresolved += n

    def ranked(c: Counter) -> list[dict]:
        return [{"name": k, "count": v} for k, v in c.most_common()]

    return {
        "days": days,
        "total": total,
        "browsers": ranked(browsers),
        "os": ranked(oses),
        "countries": [
            {"code": code, "name": name, "count": v}
            for (code, name), v in countries.most_common()
        ],
        "unresolved": unresolved,
    }
