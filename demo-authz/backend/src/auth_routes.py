"""Auth mint proxy — browser → here → Duar.

Browsers in AuthZ mode call this route to exchange an IdP token (from Google /
EntraID / etc.) + ``workspace_id`` for a Duar authz JWT. This backend holds
the service key and forwards the call to Duar's ``POST /authz/resolve``.

The browser must not call Duar's ``/authz/resolve`` directly for minting —
that endpoint rejects Origin-authenticated callers and requires an
``X-Service-Key`` (credential issuance is a server-to-server trust step).

Register this route in ``duar.protect(app, exclude_paths=[...])`` — it is
hit BEFORE the user has an authz token, so it must skip the dual-token
middleware.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.config import duar

router = APIRouter()


class MintRequest(BaseModel):
    idp_token: str
    provider: str
    workspace_id: uuid.UUID
    nonce: str | None = None


@router.post("/auth/mint")
async def mint_authz_token(body: MintRequest):
    """Proxy ``POST /authz/resolve`` with the backend's service key."""
    try:
        return await duar.authz.resolve(
            idp_token=body.idp_token,
            provider=body.provider,
            workspace_id=body.workspace_id,
            nonce=body.nonce,
        )
    except Exception as e:
        # Surface Duar's detail to the client; rely on Duar's rate
        # limits and validation for hardening.
        raise HTTPException(status_code=400, detail=str(e))
