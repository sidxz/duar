"""effective_scope = realm slug for members, else the service's own name.
Plus the service-key cache value encode/decode (now carries the realm slug)."""

import uuid


def test_effective_scope_standalone_is_service_name():
    from src.api.dependencies import ServiceKeyContext

    ctx = ServiceKeyContext(service_name="docs")
    assert ctx.realm_slug is None
    assert ctx.effective_scope == "docs"


def test_effective_scope_member_is_realm_slug():
    from src.api.dependencies import ServiceKeyContext

    ctx = ServiceKeyContext(service_name="docs", realm_slug="acme-suite")
    assert ctx.effective_scope == "acme-suite"


def test_cache_encode_decode_roundtrip_with_realm():
    from src.services.service_app_service import _decode_cache, _encode_cache

    aid = uuid.uuid4()
    assert _decode_cache(_encode_cache("docs", aid, "acme-suite")) == (
        "docs",
        aid,
        "acme-suite",
    )


def test_cache_encode_decode_no_realm():
    from src.services.service_app_service import _decode_cache, _encode_cache

    aid = uuid.uuid4()
    assert _decode_cache(_encode_cache("docs", aid, None)) == ("docs", aid, None)


def test_cache_decode_legacy_two_part_value():
    """Pre-upgrade cache entries were 'service_name:app_id' (no realm). Must not crash."""
    from src.services.service_app_service import _decode_cache

    aid = uuid.uuid4()
    assert _decode_cache(f"docs:{aid}") == ("docs", aid, None)
