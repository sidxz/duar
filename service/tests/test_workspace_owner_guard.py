"""Last-owner guard: _count_owners must not pair FOR UPDATE with an aggregate.

Postgres hard-errors on `SELECT count(*) ... FOR UPDATE` ("FOR UPDATE is not
allowed with aggregate functions"), which made every owner demotion/removal
500 before the fix. The lock must go on the owner rows, counted client-side.
"""

import uuid

import pytest
from sqlalchemy.dialects import postgresql

from src.services import workspace_service


class _CapturingSession:
    def __init__(self, rows):
        self._rows = rows
        self.stmt = None

    async def execute(self, stmt):
        self.stmt = stmt
        rows = self._rows

        class _Result:
            def scalars(self):
                class _Scalars:
                    def all(self_inner):
                        return rows

                return _Scalars()

        return _Result()


@pytest.mark.asyncio
async def test_count_owners_locks_rows_without_aggregate():
    session = _CapturingSession(rows=[uuid.uuid4(), uuid.uuid4()])
    count = await workspace_service._count_owners(session, uuid.uuid4())
    assert count == 2

    sql = str(session.stmt.compile(dialect=postgresql.dialect())).lower()
    assert "for update" in sql  # still locks the owner rows against races
    assert "count(" not in sql  # ...but never combined with an aggregate
