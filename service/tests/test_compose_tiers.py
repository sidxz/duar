"""docker-compose.prod.yml runs two Duar listeners: a published public one and
an UNPUBLISHED internal one. This guards the deployment contract — the internal
service-key surface must never get a published port. PyYAML resolves the `<<` merge
key, so the merged `environment` (incl. the per-service TIER override) is asserted
on the loaded mapping without needing Docker."""

from pathlib import Path

import yaml

_COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.prod.yml"


def _services() -> dict:
    return yaml.safe_load(_COMPOSE.read_text())["services"]


def test_public_listener_is_published_with_tier_public():
    svc = _services()["duar"]
    assert svc["environment"]["TIER"] == "public"
    assert svc.get("ports"), "public listener must publish a port"


def test_internal_listener_exists_unpublished_with_tier_internal():
    services = _services()
    assert "duar-internal" in services, "internal listener service must exist"
    internal = services["duar-internal"]
    assert internal["environment"]["TIER"] == "internal"
    # The whole point of the split: the internal listener has NO socket on the host.
    assert not internal.get("ports"), "internal listener must NOT publish any port"


def test_internal_listener_waits_for_public_to_migrate():
    internal = _services()["duar-internal"]
    # public + all are the only migrator tiers; internal must start after public is
    # healthy so the schema exists before it serves authz/permissions.
    assert "duar" in internal.get("depends_on", {})


def test_internal_listener_does_not_carry_session_secret():
    internal = _services()["duar-internal"]
    # Least-privilege: the session signing secret must not reach the internal
    # container (it drops Session middleware). Nulled via an explicit override.
    assert internal["environment"].get("SESSION_SECRET_KEY") == ""
