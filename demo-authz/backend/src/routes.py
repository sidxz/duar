"""Team Notes API routes — AuthZ mode demo.

Demonstrates all three authorization tiers using Duar's AuthZ mode:
  - IdP token (Authorization: Bearer <idp_token>) — proves identity
  - Authz token (X-Authz-Token: <authz_token>) — proves authorization

The client app handles IdP login directly (e.g. Google Sign-In) and calls
Duar's /authz/resolve to get the authz token. Both tokens are sent
to this backend on every request.
"""

import uuid
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from duar_auth.types import AuthenticatedUser

from src.config import duar
from src.deps import get_current_user, get_token, get_workspace_id, require_role
from src.models import notes

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------
class CreateNoteRequest(BaseModel):
    title: str
    content: str


class UpdateNoteRequest(BaseModel):
    title: str | None = None
    content: str | None = None


class ShareNoteRequest(BaseModel):
    user_id: uuid.UUID
    permission: str = "view"


# ---------------------------------------------------------------------------
# User info — extracted from dual tokens by AuthzMiddleware
# ---------------------------------------------------------------------------
@router.get("/me")
async def whoami(user: AuthenticatedUser = Depends(get_current_user)):
    """Current user context from dual-token validation."""
    return {
        "user_id": str(user.user_id),
        "email": user.email,
        "name": user.name,
        "workspace_id": str(user.workspace_id),
        "workspace_slug": user.workspace_slug,
        "workspace_role": user.workspace_role,
    }


# ---------------------------------------------------------------------------
# RBAC actions — uses RoleClient to list available actions (Tier 2)
# ---------------------------------------------------------------------------
@router.get("/me/actions")
async def my_actions(
    user: AuthenticatedUser = Depends(get_current_user),
    token: str = Depends(get_token),
):
    """List all RBAC actions available to the current user."""
    actions = await duar.roles.get_user_actions(token, user.workspace_id)
    return {"actions": actions}


# ---------------------------------------------------------------------------
# Tier 1: Workspace role — list notes (any authenticated user)
# ---------------------------------------------------------------------------
@router.get("/notes")
async def list_notes(workspace_id: uuid.UUID = Depends(get_workspace_id)):
    """List all notes in the current workspace."""
    return [asdict(n) for n in notes.list_by_workspace(workspace_id)]


# ---------------------------------------------------------------------------
# Tier 2: Custom RBAC — export notes (requires notes:export action)
# NOTE: Must be defined before /notes/{note_id} to avoid path conflict
# ---------------------------------------------------------------------------
@router.get("/notes/export")
async def export_notes(
    user: AuthenticatedUser = Depends(duar.require_action("notes:export")),
    workspace_id: uuid.UUID = Depends(get_workspace_id),
):
    """Export all workspace notes. Requires 'notes:export' RBAC action."""
    workspace_notes = notes.list_by_workspace(workspace_id)
    return {
        "format": "json",
        "count": len(workspace_notes),
        "notes": [asdict(n) for n in workspace_notes],
    }


# ---------------------------------------------------------------------------
# Tier 1: Workspace role — create note (editor+)
# Tier 3: Entity ACL — registers resource with PermissionClient
# ---------------------------------------------------------------------------
@router.post("/notes", status_code=201)
async def create_note(
    body: CreateNoteRequest,
    user: AuthenticatedUser = Depends(require_role("editor")),
):
    """Create a note. Requires at least 'editor' workspace role."""
    note = notes.create(
        title=body.title,
        content=body.content,
        workspace_id=user.workspace_id,
        owner_id=user.user_id,
        owner_name=user.name,
    )

    await duar.permissions.register_resource(
        resource_type="note",
        resource_id=note.id,
        workspace_id=user.workspace_id,
        owner_id=user.user_id,
        visibility="workspace",
    )

    return asdict(note)


# ---------------------------------------------------------------------------
# Tier 3: Entity ACL — view a single note (checks 'view' permission)
# ---------------------------------------------------------------------------
@router.get("/notes/{note_id}")
async def get_note(
    note_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    token: str = Depends(get_token),
):
    """View a note. Checks entity-level 'view' permission."""
    note = notes.get(note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    allowed = await duar.permissions.can(
        token=token,
        resource_type="note",
        resource_id=note_id,
        action="view",
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="Access denied")

    return asdict(note)


# ---------------------------------------------------------------------------
# Tier 3: Entity ACL — update a note (checks 'edit' permission)
# ---------------------------------------------------------------------------
@router.patch("/notes/{note_id}")
async def update_note(
    note_id: uuid.UUID,
    body: UpdateNoteRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    token: str = Depends(get_token),
):
    """Update a note. Checks entity-level 'edit' permission."""
    note = notes.get(note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    allowed = await duar.permissions.can(
        token=token,
        resource_type="note",
        resource_id=note_id,
        action="edit",
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="Access denied")

    updated = notes.update(note_id, title=body.title, content=body.content)
    return asdict(updated)


# ---------------------------------------------------------------------------
# Tier 1: Workspace role — delete note (admin+)
# ---------------------------------------------------------------------------
@router.delete("/notes/{note_id}")
async def delete_note(
    note_id: uuid.UUID,
    user: AuthenticatedUser = Depends(require_role("admin")),
):
    """Delete a note. Requires at least 'admin' workspace role."""
    if not notes.delete(note_id):
        raise HTTPException(status_code=404, detail="Note not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Tier 3: Entity ACL — share a note (owner grants permission to another user)
# ---------------------------------------------------------------------------
@router.post("/notes/{note_id}/share")
async def share_note(
    note_id: uuid.UUID,
    body: ShareNoteRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    token: str = Depends(get_token),
):
    """Share a note with another user. Only the note owner can share."""
    note = notes.get(note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    if note.owner_id != user.user_id:
        raise HTTPException(status_code=403, detail="Only the owner can share")

    try:
        await duar.permissions.share(
            token=token,
            resource_type="note",
            resource_id=note_id,
            grantee_type="user",
            grantee_id=body.user_id,
            permission=body.permission,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"ok": True, "shared_with": str(body.user_id), "permission": body.permission}
