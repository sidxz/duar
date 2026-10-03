"""ServiceAppResponse exposes realm_id so the admin UI can show realm membership."""

import uuid
from datetime import UTC, datetime


def test_service_app_response_carries_realm_id():
    from src.schemas.service_app import ServiceAppResponse

    rid = uuid.uuid4()
    resp = ServiceAppResponse(
        id=uuid.uuid4(),
        name="Docs",
        service_name="docs",
        key_prefix="sk_xxxx****",
        is_active=True,
        allowed_origins=[],
        allowed_idp_audiences=[],
        last_used_at=None,
        created_by=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        realm_id=rid,
    )
    assert resp.realm_id == rid


def test_service_app_response_realm_id_defaults_none():
    from src.schemas.service_app import ServiceAppResponse

    resp = ServiceAppResponse(
        id=uuid.uuid4(),
        name="Docs",
        service_name="docs",
        key_prefix="sk_xxxx****",
        is_active=True,
        allowed_origins=[],
        allowed_idp_audiences=[],
        last_used_at=None,
        created_by=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert resp.realm_id is None
