"""The Realm model and the service_apps.realm_id membership FK."""

import uuid


def test_realm_has_expected_columns():
    from src.models.realm import Realm

    cols = {c.name for c in Realm.__table__.columns}
    assert cols == {
        "id",
        "slug",
        "name",
        "m2m_ttl_s",
        "is_active",
        "created_by",
        "created_at",
        "updated_at",
    }
    assert Realm.__table__.c.slug.unique is True


def test_realm_instance_carries_fields():
    from src.models.realm import Realm

    r = Realm(id=uuid.uuid4(), name="Acme Suite", slug="acme-suite", m2m_ttl_s=300)
    assert r.slug == "acme-suite"
    assert r.name == "Acme Suite"
    assert r.m2m_ttl_s == 300


def test_service_app_has_realm_id():
    from src.models.service_app import ServiceApp

    assert "realm_id" in ServiceApp.__table__.columns
    app = ServiceApp(
        id=uuid.uuid4(),
        name="Docs",
        service_name="docs",
        key_hash="x" * 64,
        key_prefix="sk_xxxx****",
        allowed_origins=[],
        allowed_idp_audiences=[],
    )
    assert app.realm_id is None
