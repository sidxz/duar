"""X-Forwarded-For trust: only the trusted-proxy hop may set the client IP.

Regression for the rate-limit bypass where the leftmost (client-controlled)
XFF value was trusted, letting an attacker rotate it to evade per-IP limits.
"""

from types import SimpleNamespace

from src.config import settings
from src.middleware.rate_limit import get_client_ip


class _Headers(dict):
    def get(self, key, default=None):
        return super().get(key.lower(), default)


def _req(xff=None, client_host="203.0.113.9"):
    headers = _Headers()
    if xff is not None:
        headers["x-forwarded-for"] = xff
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host=client_host))


def test_not_behind_proxy_ignores_xff(monkeypatch):
    monkeypatch.setattr(settings, "behind_proxy", False)
    req = _req(xff="1.2.3.4", client_host="10.0.0.1")
    assert get_client_ip(req) == "10.0.0.1"


def test_single_trusted_proxy_uses_rightmost_hop(monkeypatch):
    monkeypatch.setattr(settings, "behind_proxy", True)
    monkeypatch.setattr(settings, "trusted_proxy_count", 1)
    # Attacker prepends a spoofed value; the trusted proxy appends the real peer.
    req = _req(xff="1.2.3.4, 198.51.100.7")
    assert get_client_ip(req) == "198.51.100.7"


def test_spoofed_leftmost_is_ignored(monkeypatch):
    monkeypatch.setattr(settings, "behind_proxy", True)
    monkeypatch.setattr(settings, "trusted_proxy_count", 1)
    # Rotating the leftmost spoof must not change the bucket — rightmost is fixed.
    assert get_client_ip(_req(xff="evilA, 198.51.100.7")) == "198.51.100.7"
    assert get_client_ip(_req(xff="evilB, 198.51.100.7")) == "198.51.100.7"


def test_two_trusted_proxies_uses_second_from_right(monkeypatch):
    monkeypatch.setattr(settings, "behind_proxy", True)
    monkeypatch.setattr(settings, "trusted_proxy_count", 2)
    # client, real-client-to-edge, edge-to-nginx
    req = _req(xff="spoof, 198.51.100.7, 10.0.0.2")
    assert get_client_ip(req) == "198.51.100.7"


def test_short_chain_falls_back_to_peer(monkeypatch):
    monkeypatch.setattr(settings, "behind_proxy", True)
    monkeypatch.setattr(settings, "trusted_proxy_count", 2)
    # Only one hop present but two trusted expected → cannot trust XFF, use peer.
    req = _req(xff="just-one", client_host="10.0.0.9")
    assert get_client_ip(req) == "10.0.0.9"
