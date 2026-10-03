"""Validation rules on the org admin request schemas."""

import uuid

import pytest
from pydantic import ValidationError

from src.schemas.admin import (
    AdminOrgCreateRequest,
    AdminOrgDomainCreateRequest,
    AdminOrgUpdateRequest,
    AdminWorkspaceAllowedOrgsRequest,
)


def test_org_create_accepts_valid_slug():
    req = AdminOrgCreateRequest(name="TAMU", slug="tamu")
    assert req.slug == "tamu"


@pytest.mark.parametrize("bad", ["", "   ", "<b></b>"])
def test_org_create_rejects_blank_name(bad):
    # Name must survive HTML-stripping non-empty (no whitespace/markup-only names).
    with pytest.raises(ValidationError):
        AdminOrgCreateRequest(name=bad, slug="tamu")


@pytest.mark.parametrize("bad", ["", "   ", "<b></b>"])
def test_org_update_rejects_blank_name(bad):
    # PATCH must not be able to blank an org's display name.
    with pytest.raises(ValidationError):
        AdminOrgUpdateRequest(name=bad)


def test_org_update_allows_omitted_name():
    # name is optional on update; omitting it (enabled-only PATCH) is valid.
    req = AdminOrgUpdateRequest(enabled=False)
    assert req.name is None
    assert req.enabled is False


@pytest.mark.parametrize("bad", ["Tamu", "-tamu", "tamu-", "ta mu", "a", "t@mu"])
def test_org_create_rejects_bad_slug(bad):
    with pytest.raises(ValidationError):
        AdminOrgCreateRequest(name="X", slug=bad)


def test_domain_request_defaults_include_subdomains_false():
    req = AdminOrgDomainCreateRequest(domain="tamu.edu")
    assert req.include_subdomains is False


def test_allowed_orgs_request_takes_uuid_list():
    ids = [uuid.uuid4(), uuid.uuid4()]
    req = AdminWorkspaceAllowedOrgsRequest(organization_ids=ids)
    assert req.organization_ids == ids


def test_allowed_orgs_request_caps_list_size():
    with pytest.raises(ValidationError):
        AdminWorkspaceAllowedOrgsRequest(
            organization_ids=[uuid.uuid4() for _ in range(501)]
        )
