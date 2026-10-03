"""Tests: sign-in insights — UA family parsing + device/geo aggregation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services import insights_service
from src.services.insights_service import parse_user_agent

MAC_CHROME = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)
WIN_EDGE = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0"
)
IPAD_SAFARI = (
    "Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)


@pytest.mark.parametrize(
    ("ua", "browser", "os"),
    [
        (MAC_CHROME, "Chrome", "macOS"),
        (WIN_EDGE, "Edge", "Windows"),  # Edg/ must win over Chrome/
        (IPAD_SAFARI, "Safari", "iOS"),  # iPad "like Mac OS X" must not read as macOS
        (
            "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
            "Firefox",
            "Linux",
        ),
        (
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36",
            "Chrome",
            "Android",
        ),
        ("curl/8.7.1", "curl", "Other"),
        ("python-requests/2.32.0", "Python client", "Other"),
        ("", "Unknown", "Unknown"),
    ],
)
def test_parse_user_agent(ua, browser, os):
    assert parse_user_agent(ua) == (browser, os)


@pytest.mark.asyncio
async def test_signin_insights_aggregates_devices_and_countries():
    rows = MagicMock()
    rows.all.return_value = [
        ("8.8.8.8", MAC_CHROME, 10),
        ("81.2.69.142", WIN_EDGE, 4),
        ("8.8.8.8", WIN_EDGE, 2),
        ("127.0.0.1", MAC_CHROME, 3),  # private → unresolved
    ]
    db = MagicMock()
    db.execute = AsyncMock(return_value=rows)

    def fake_geo(ip):
        return {
            "8.8.8.8": ("US", "United States"),
            "81.2.69.142": ("GB", "United Kingdom"),
        }.get(ip)

    with patch.object(insights_service, "_lookup_country", side_effect=fake_geo):
        out = await insights_service.signin_insights(db, days=30)

    assert out["total"] == 19
    assert out["browsers"] == [
        {"name": "Chrome", "count": 13},
        {"name": "Edge", "count": 6},
    ]
    assert out["os"] == [
        {"name": "macOS", "count": 13},
        {"name": "Windows", "count": 6},
    ]
    assert out["countries"] == [
        {"code": "US", "name": "United States", "count": 12},
        {"code": "GB", "name": "United Kingdom", "count": 4},
    ]
    assert out["unresolved"] == 3
