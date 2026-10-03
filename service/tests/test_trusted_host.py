"""/health is exempt from TrustedHost validation.

k8s liveness/readiness probes hit the pod IP directly, so their Host header can
never be on the ALLOWED_HOSTS allowlist — without the exemption the probe gets a
400, fails, and kubelet kills an otherwise-healthy pod. /health returns a static
body, so skipping host validation there exposes nothing.
"""

from fastapi.testclient import TestClient

from src.config import settings
from src.main import create_app


def test_health_exempt_from_host_check_other_paths_still_enforced(monkeypatch):
    monkeypatch.setattr(settings, "allowed_hosts", "example.com")
    client = TestClient(create_app("all"))
    bad_host = {"Host": "10.244.1.7:9003"}  # pod-IP probe, not on the allowlist
    assert client.get("/health", headers=bad_host).status_code == 200
    assert client.get("/", headers=bad_host).status_code == 400
    assert client.get("/health", headers={"Host": "example.com"}).status_code == 200
