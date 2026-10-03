"""Tests: daily_counts shaping + target-label resolution for the activity UI."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services import activity_service


@pytest.mark.asyncio
async def test_attach_target_labels_resolves_known_and_skips_unknown():
    user_id, missing_id = uuid.uuid4(), uuid.uuid4()
    user = MagicMock()
    user.id = user_id
    user.name = "Ada"
    result = MagicMock()
    result.scalars.return_value = [user]
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)

    items = [
        {"target_type": "user", "target_id": user_id},
        {"target_type": "user", "target_id": missing_id},  # deleted target
        {"target_type": "system", "target_id": uuid.UUID(int=0)},  # unmapped type
    ]
    await activity_service._attach_target_labels(db, items)

    assert items[0]["target_label"] == "Ada"
    assert items[1]["target_label"] is None
    assert items[2]["target_label"] is None
    # one batched query for the one resolvable type on the page
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_daily_counts_shapes_rows():
    db = MagicMock()
    rows = MagicMock()
    rows.all.return_value = [
        (datetime(2026, 7, 20, tzinfo=UTC), "user_login", 5),
        (datetime(2026, 7, 20, tzinfo=UTC), "login_failed", 2),
        (datetime(2026, 7, 21, tzinfo=UTC), "workspace_created", 1),
    ]
    db.execute = AsyncMock(return_value=rows)

    out = await activity_service.daily_counts(db, days=30)

    assert out == [
        {"day": "2026-07-20", "action": "user_login", "count": 5},
        {"day": "2026-07-20", "action": "login_failed", "count": 2},
        {"day": "2026-07-21", "action": "workspace_created", "count": 1},
    ]
