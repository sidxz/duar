"""register_actions: atomic, idempotent upsert (no read-then-insert lock window).

These exercise the service with a fake session in the repo's existing style.
They assert the *shape* of the DB interaction — a single ON CONFLICT upsert plus
a read-back — which is what prevents the stranded-lock startup ReadTimeout the
non-atomic version could cause. Postgres ON CONFLICT semantics themselves are a
DB concern, not re-tested here.
"""

import uuid

import pytest
from sqlalchemy.dialects import postgresql

from src.models.role import ServiceAction
from src.services import role_service


class _ExecResult:
    def __init__(self, scalars=None):
        self._scalars = scalars or []

    def scalars(self):
        parent = self

        class _S:
            def all(self_inner):
                return parent._scalars

        return _S()


class _RecordingSession:
    """Serves queued execute() results; records statements and commit count."""

    def __init__(self, exec_results):
        self._exec = list(exec_results)
        self.statements = []
        self.commits = 0

    async def execute(self, stmt):
        self.statements.append(stmt)
        return self._exec.pop(0)

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_empty_actions_short_circuits_without_db():
    session = _RecordingSession(exec_results=[])
    result = await role_service.register_actions(session, "svc", [])
    assert result == []
    assert session.statements == []  # no round-trip, no lock taken
    assert session.commits == 0


@pytest.mark.asyncio
async def test_registers_via_single_upsert_then_reads_back():
    existing = ServiceAction(
        id=uuid.uuid4(), service_name="svc", action="svc:read", description="Read"
    )
    session = _RecordingSession(
        exec_results=[
            _ExecResult(),  # the upsert (return value is ignored by the service)
            _ExecResult(scalars=[existing]),  # the read-back select
        ]
    )

    result = await role_service.register_actions(
        session, "svc", [{"action": "svc:read", "description": "Read"}]
    )

    assert result == [existing]
    assert session.commits == 1
    # Exactly one write + one read-back — NOT a per-row read-then-insert.
    assert len(session.statements) == 2

    compiled = str(session.statements[0].compile(dialect=postgresql.dialect()))
    # A genuine atomic upsert, and a NULL description must not clobber an existing
    # one (coalesce(excluded, existing)).
    assert "ON CONFLICT" in compiled.upper()
    assert "coalesce" in compiled.lower()
    assert "service_actions" in compiled


@pytest.mark.asyncio
async def test_duplicate_actions_in_batch_are_deduped():
    # A single INSERT ... ON CONFLICT DO UPDATE cannot affect the same target row
    # twice ("cannot affect row a second time"), so a batch repeating an action
    # must be collapsed (last description wins) before the upsert is built.
    session = _RecordingSession(
        exec_results=[
            _ExecResult(),  # upsert
            _ExecResult(scalars=[]),  # read-back
        ]
    )

    await role_service.register_actions(
        session,
        "svc",
        [
            {"action": "svc:read", "description": "A"},
            {"action": "svc:read", "description": "B"},
        ],
    )

    assert session.commits == 1
    assert len(session.statements) == 2  # one upsert + one read-back, not per-row

    params = session.statements[0].compile(dialect=postgresql.dialect()).params
    values = list(params.values())
    assert values.count("svc:read") == 1  # the action is inserted once (deduped)
    assert "B" in values and "A" not in values  # last description wins
