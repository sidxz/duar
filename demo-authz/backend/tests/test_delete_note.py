"""Deleting a note also removes its permission record in Duar."""

import uuid

import pytest
from fastapi import HTTPException

from duar_auth.types import AuthenticatedUser, DuarError
from src import routes
from src.models import notes


def _admin(workspace_id: uuid.UUID) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid.uuid4(),
        email="a@b.c",
        name="A",
        workspace_id=workspace_id,
        workspace_slug="ws",
        workspace_role="admin",
    )


def _note(workspace_id: uuid.UUID):
    return notes.create(
        title="t",
        content="c",
        workspace_id=workspace_id,
        owner_id=uuid.uuid4(),
        owner_name="A",
    )


@pytest.fixture
def deregistered(monkeypatch):
    """Stand-in for the HTTP call to Duar; records what would be deregistered."""
    calls: list[tuple] = []

    async def fake(resource_type, resource_id):
        calls.append((resource_type, resource_id))

    monkeypatch.setattr(routes.duar.permissions, "deregister_resource", fake)
    return calls


async def test_deleting_a_note_deregisters_it_in_duar(deregistered):
    workspace = uuid.uuid4()
    note = _note(workspace)

    assert await routes.delete_note(note.id, _admin(workspace)) == {"ok": True}

    assert notes.get(note.id) is None
    assert deregistered == [("note", note.id)]


async def test_note_is_kept_when_duar_cannot_deregister_it(monkeypatch):
    async def unavailable(resource_type, resource_id):
        raise RuntimeError("duar unavailable")

    monkeypatch.setattr(routes.duar.permissions, "deregister_resource", unavailable)
    workspace = uuid.uuid4()
    note = _note(workspace)

    with pytest.raises(RuntimeError):
        await routes.delete_note(note.id, _admin(workspace))

    assert notes.get(note.id) is not None  # nothing half-deleted; the delete can be retried


async def test_a_note_duar_has_no_record_of_can_still_be_deleted(monkeypatch):
    async def gone(resource_type, resource_id):
        raise DuarError("Duar API error: 404", status_code=404)

    monkeypatch.setattr(routes.duar.permissions, "deregister_resource", gone)
    workspace = uuid.uuid4()
    note = _note(workspace)

    assert await routes.delete_note(note.id, _admin(workspace)) == {"ok": True}
    assert notes.get(note.id) is None


async def test_note_is_kept_when_duar_refuses_for_another_reason(monkeypatch):
    async def refused(resource_type, resource_id):
        raise DuarError("Duar API error: 403", status_code=403)

    monkeypatch.setattr(routes.duar.permissions, "deregister_resource", refused)
    workspace = uuid.uuid4()
    note = _note(workspace)

    with pytest.raises(DuarError):
        await routes.delete_note(note.id, _admin(workspace))

    assert notes.get(note.id) is not None


async def test_admin_of_another_workspace_cannot_delete_the_note(deregistered):
    note = _note(uuid.uuid4())

    with pytest.raises(HTTPException) as raised:
        await routes.delete_note(note.id, _admin(uuid.uuid4()))

    assert raised.value.status_code == 404
    assert notes.get(note.id) is not None
    assert deregistered == []


async def test_deleting_an_unknown_note_is_404_and_leaves_duar_alone(deregistered):
    with pytest.raises(HTTPException) as raised:
        await routes.delete_note(uuid.uuid4(), _admin(uuid.uuid4()))

    assert raised.value.status_code == 404
    assert deregistered == []
