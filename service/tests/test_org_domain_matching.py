"""Organization model shape + pure domain-matching logic."""

import uuid

import pytest

from src.services import organization_service as org_svc


def test_organization_models_have_expected_shape():
    from src.models.organization import (
        Organization,
        OrganizationDomain,
        WorkspaceAllowedOrganization,
    )

    assert Organization.__tablename__ == "organizations"
    for col in ("id", "slug", "name", "is_public", "enabled", "created_by"):
        assert col in Organization.__table__.columns
    # At most one public org — the partial unique index must exist.
    assert any(
        idx.name == "uq_one_public_org" for idx in Organization.__table__.indexes
    )

    assert OrganizationDomain.__tablename__ == "organization_domains"
    for col in ("id", "organization_id", "domain", "include_subdomains"):
        assert col in OrganizationDomain.__table__.columns
    # Domain must be globally unique (a domain cannot belong to two orgs).
    assert any(
        c.name == "uq_org_domain" for c in OrganizationDomain.__table__.constraints
    )

    assert (
        WorkspaceAllowedOrganization.__tablename__ == "workspace_allowed_organizations"
    )
    for col in ("id", "workspace_id", "organization_id"):
        assert col in WorkspaceAllowedOrganization.__table__.columns


def test_models_registered_for_metadata():
    import src.models as m

    assert "Organization" in m.__all__
    assert "OrganizationDomain" in m.__all__
    assert "WorkspaceAllowedOrganization" in m.__all__


def test_user_has_organization_id_column():
    from src.models.user import User

    assert "organization_id" in User.__table__.columns
    assert User.__table__.columns["organization_id"].nullable is True


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Alice@TAMU.edu", "tamu.edu"),
        ("tamu.edu", "tamu.edu"),
        ("bob@mail.tamu.edu", "mail.tamu.edu"),
        ("  spaced@Example.COM  ", "example.com"),
        ("", None),
        (None, None),
        ("noatsign-nodot", None),
        ("a@b@c.com", None),  # multiple '@' is malformed -> fail closed
        ("user@", None),  # empty domain
        ("tamu.edu.", None),  # trailing dot
        ("@tamu.edu", "tamu.edu"),  # leading '@' ok, single '@'
        ("a@\x00.com", None),  # null byte -> fail closed
        ("a@" + "x" * 250 + ".com", None),  # over RFC 1035 253-char limit
        # Hardened: idna passes pure-ASCII junk through, so the LDH backstop must
        # reject URLs, ports, spaces, and comma-lists.
        ("https://tamu.edu", None),
        ("tamu.edu:443", None),
        ("exa mple.com", None),
        ("tamu.edu, tamu.org", None),
        # Hardened: a Unicode full-stop variant (U+3002) maps to '.' during idna
        # encoding; it must not split into an extra label that suffix-matches a
        # victim org's subdomain rule, nor sneak past the trailing-dot check.
        ("evil.com。tamu.edu", None),
        ("user@evil.com。tamu.edu", None),
        ("tamu.edu。", None),
    ],
)
def test_normalize_domain(raw, expected):
    assert org_svc.normalize_domain(raw) == expected


def test_match_exact_wins():
    a = uuid.uuid4()
    rows = [(a, "tamu.edu", False)]
    assert org_svc.match_org_id("tamu.edu", rows) == a


def test_match_is_case_insensitive_on_input():
    a = uuid.uuid4()
    # match_org_id lowercases its input defensively.
    assert org_svc.match_org_id("TAMU.edu", [(a, "tamu.edu", False)]) == a


def test_match_subdomain_only_when_flag_set():
    a = uuid.uuid4()
    assert org_svc.match_org_id("mail.tamu.edu", [(a, "tamu.edu", True)]) == a
    assert org_svc.match_org_id("mail.tamu.edu", [(a, "tamu.edu", False)]) is None


def test_match_longest_subdomain_wins():
    a, b = uuid.uuid4(), uuid.uuid4()
    rows = [(a, "tamu.edu", True), (b, "b.tamu.edu", True)]
    assert org_svc.match_org_id("a.b.tamu.edu", rows) == b


def test_match_exact_beats_subdomain_regardless_of_order():
    a, b = uuid.uuid4(), uuid.uuid4()
    rows = [(b, "edu", True), (a, "tamu.edu", False)]
    assert org_svc.match_org_id("tamu.edu", rows) == a


def test_match_anti_spoof_not_a_real_subdomain():
    a = uuid.uuid4()
    # "eviltamu.edu" must NOT match "tamu.edu" even with subdomains on.
    assert org_svc.match_org_id("eviltamu.edu", [(a, "tamu.edu", True)]) is None


def test_match_no_rows():
    assert org_svc.match_org_id("gmail.com", []) is None
