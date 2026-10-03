"""In-memory note storage. No database needed for the demo."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class Note:
    id: uuid.UUID
    title: str
    content: str
    workspace_id: uuid.UUID
    owner_id: uuid.UUID
    owner_name: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class NoteStore:
    """Workspace-scoped in-memory note store."""

    def __init__(self) -> None:
        self._notes: dict[uuid.UUID, Note] = {}

    def list_by_workspace(self, workspace_id: uuid.UUID) -> list[Note]:
        return [
            n for n in self._notes.values() if n.workspace_id == workspace_id
        ]

    def get(self, note_id: uuid.UUID) -> Note | None:
        return self._notes.get(note_id)

    def create(
        self,
        title: str,
        content: str,
        workspace_id: uuid.UUID,
        owner_id: uuid.UUID,
        owner_name: str,
    ) -> Note:
        note = Note(
            id=uuid.uuid4(),
            title=title,
            content=content,
            workspace_id=workspace_id,
            owner_id=owner_id,
            owner_name=owner_name,
        )
        self._notes[note.id] = note
        return note

    def update(
        self,
        note_id: uuid.UUID,
        title: str | None = None,
        content: str | None = None,
    ) -> Note | None:
        note = self._notes.get(note_id)
        if not note:
            return None
        if title is not None:
            note.title = title
        if content is not None:
            note.content = content
        note.updated_at = datetime.now(UTC)
        return note

    def delete(self, note_id: uuid.UUID) -> bool:
        return self._notes.pop(note_id, None) is not None


notes = NoteStore()
