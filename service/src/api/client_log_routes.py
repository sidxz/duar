"""Authenticated ingest for admin-SPA client logs. Client input is untrusted:
bounded, allowlisted, and PII-redacted before re-emitting into the log stream.
"""

import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator

from src.api.dependencies import require_admin
from src.config import settings
from src.logging_redaction import redact_mapping
from src.middleware.rate_limit import get_client_ip, limiter, user_or_ip_key

router = APIRouter(prefix="/internal", tags=["internal"])

_VALID_LEVELS = {"debug", "info", "warning", "error"}
_SECURITY_EVENTS = {"client.login.failed", "client.auth.denied"}

# Keys that collide with structlog reserved kwargs OR with the kwargs we set
# ourselves below (category/client_origin/actor/source_ip). Dropped before the
# client-supplied fields are **-unpacked, so a client field can never cause a
# "got multiple values for keyword argument" TypeError.
_STRUCTLOG_RESERVED = frozenset(
    {"event", "level", "category", "client_origin", "actor", "source_ip"}
)


class ClientEvent(BaseModel):
    event: str = Field(max_length=80)
    level: str = "info"
    fields: dict = Field(default_factory=dict)

    @field_validator("event")
    @classmethod
    def _client_namespaced(cls, v: str) -> str:
        if not v.startswith("client."):
            raise ValueError("event must be client.*-namespaced")
        return v

    @field_validator("level")
    @classmethod
    def _known_level(cls, v: str) -> str:
        if v not in _VALID_LEVELS:
            raise ValueError("invalid level")
        return v

    @field_validator("fields")
    @classmethod
    def _bounded_fields(cls, v: dict) -> dict:
        if len(v) > 20:
            raise ValueError("too many fields")
        # Drop keys that would collide with structlog reserved kwargs
        for k in _STRUCTLOG_RESERVED:
            v.pop(k, None)
        return v


class ClientLogBatch(BaseModel):
    events: list[ClientEvent] = Field(max_length=50)


@router.post("/client-logs", status_code=202)
@limiter.limit(settings.rate_limit_read, key_func=user_or_ip_key)
async def ingest_client_logs(
    request: Request,
    batch: ClientLogBatch,
    admin=Depends(require_admin),
):
    log = structlog.get_logger()
    source_ip = get_client_ip(request)
    for ev in batch.events:
        category = "security" if ev.event in _SECURITY_EVENTS else "app"
        payload = redact_mapping(ev.fields)
        getattr(log, ev.level)(
            ev.event,
            category=category,
            client_origin=True,
            actor=admin.get("sub"),
            source_ip=source_ip,
            **payload,
        )
    return {"accepted": len(batch.events)}
