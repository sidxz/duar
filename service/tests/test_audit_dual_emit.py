import uuid

import pytest
from structlog.testing import capture_logs

from src.services import activity_service


class _FakeDB:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass


@pytest.mark.asyncio
async def test_log_activity_writes_db_and_emits_event():
    db = _FakeDB()
    actor_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    target_id = uuid.uuid4()
    detail = {"name": "Acme"}
    with capture_logs() as logs:
        await activity_service.log_activity(
            db,
            action="workspace_created",
            target_type="workspace",
            target_id=target_id,
            actor_id=actor_id,
            workspace_id=workspace_id,
            detail=detail,
        )
    assert db.added  # DB row still created
    audit = [e for e in logs if e["event"] == "audit.activity"]
    assert audit
    assert audit[0]["category"] == "audit"
    assert audit[0]["action"] == "workspace_created"
    assert audit[0]["target_type"] == "workspace"
    assert audit[0]["detail"] == detail
    assert isinstance(audit[0]["actor"], str)
    assert isinstance(audit[0]["workspace_id"], str)


@pytest.mark.asyncio
async def test_log_activity_survives_emit_failure(monkeypatch):
    """Prove that log_activity succeeds even if log_audit raises."""

    def _boom(*a, **k):
        raise RuntimeError("structlog down")

    monkeypatch.setattr(activity_service, "log_audit", _boom)
    db = _FakeDB()
    entry = await activity_service.log_activity(
        db,
        action="x",
        target_type="t",
        target_id=uuid.uuid4(),
    )
    assert db.added  # DB row still created despite emit failure
    assert entry is not None


def _sync_session():
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    session = Session(create_engine("sqlite://"))
    session.execute(text("SELECT 1"))  # ensure a transaction is active
    return session


def test_audit_emit_is_deferred_to_commit(monkeypatch):
    """With a real ORM session the audit event fires on commit, not before."""
    emitted = []
    monkeypatch.setattr(activity_service, "log_audit", lambda **kw: emitted.append(kw))

    session = _sync_session()
    activity_service._register_commit_flush(session)
    session.info.setdefault("pending_audit", []).append({"action": "workspace_created"})

    assert emitted == []  # nothing emitted before commit
    session.commit()
    assert emitted == [{"action": "workspace_created"}]  # emitted on commit


def test_audit_emit_discarded_on_rollback(monkeypatch):
    """A rolled-back transaction must not emit a phantom audit event."""
    emitted = []
    monkeypatch.setattr(activity_service, "log_audit", lambda **kw: emitted.append(kw))

    session = _sync_session()
    activity_service._register_commit_flush(session)
    session.info.setdefault("pending_audit", []).append({"action": "workspace_created"})

    session.rollback()
    assert emitted == []  # discarded
    session.commit()
    assert emitted == []  # nothing stranded to emit later
