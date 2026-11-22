"""AuthZ-mode sign-ins are recorded: the discovery call writes one user_login row
and runs the login signals; mints (refreshes, per-request) do not."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.api import authz_routes
from src.models.activity import ActivityLog
from tests.test_authz_token_groups import SVC, _env, _mint, client, seeded  # noqa: F401


@pytest.mark.asyncio
async def test_discovery_records_one_sign_in_mint_does_not(client, seeded, monkeypatch):  # noqa: F811
    signals = []

    async def _signals(_db, **kw):
        signals.append(kw)

    monkeypatch.setattr(authz_routes.signal_service, "on_login_success", _signals)

    resp = client.post(
        "/authz/resolve",
        json={"idp_token": "x", "provider": "google"},
        headers={"User-Agent": "ua-test"},
    )
    assert resp.status_code == 200, resp.text
    _mint(client, seeded["ws"].id)

    rows = (
        (
            await seeded["db"].execute(
                select(ActivityLog).where(ActivityLog.action == "user_login")
            )
        )
        .scalars()
        .all()
    )
    user_id = seeded["user"].id
    assert [r.actor_id for r in rows] == [user_id]
    assert rows[0].detail["flow"] == "authz"
    assert rows[0].detail["service"] == SVC
    assert rows[0].detail["user_agent"] == "ua-test"
    assert [s["user_id"] for s in signals] == [user_id]
