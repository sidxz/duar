"""Realm admin schemas: letter-start slug validation, ttl bounds, member shape."""

import uuid

import pytest
from pydantic import ValidationError


def test_create_accepts_valid_slug_and_defaults_ttl():
    from src.schemas.realm import RealmCreateRequest

    body = RealmCreateRequest(name="Acme Suite", slug="acme-suite")
    assert body.slug == "acme-suite"
    assert body.m2m_ttl_s == 300


def test_create_rejects_digit_start_slug():
    from src.schemas.realm import RealmCreateRequest

    with pytest.raises(ValidationError):
        RealmCreateRequest(name="X", slug="9acme")


def test_create_rejects_out_of_range_ttl():
    from src.schemas.realm import RealmCreateRequest

    with pytest.raises(ValidationError):
        RealmCreateRequest(name="X", slug="acme-suite", m2m_ttl_s=5)  # below floor


def test_update_all_fields_optional():
    from src.schemas.realm import RealmUpdateRequest

    body = RealmUpdateRequest()
    assert body.name is None and body.m2m_ttl_s is None and body.is_active is None


def test_update_has_no_slug_field():
    from src.schemas.realm import RealmUpdateRequest

    assert "slug" not in RealmUpdateRequest.model_fields  # slug is immutable


def test_member_response_shape():
    from src.schemas.realm import RealmMemberResponse

    m = RealmMemberResponse(
        id=uuid.uuid4(), name="Docs", service_name="docs", has_grants=True
    )
    assert m.service_name == "docs"
    assert m.has_grants is True
