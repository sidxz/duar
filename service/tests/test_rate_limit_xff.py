"""X-Forwarded-For trust: only the trusted-proxy hop may set the client IP.

Regression for the rate-limit bypass where the leftmost (client-controlled)
XFF value was trusted, letting an attacker rotate it to evade per-IP limits.
"""

from starlette.requests import Request

from src.config import settings
from src.middleware.rate_limit import get_client_ip


def _req(*xff, client_host="203.0.113.9"):
    """A request carrying one ``X-Forwarded-For`` line per value in ``xff``."""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "headers": [(b"x-forwarded-for", line.encode()) for line in xff],
            "client": (client_host, 5555),
        }
    )


def test_not_behind_proxy_ignores_xff(monkeypatch):
    monkeypatch.setattr(settings, "behind_proxy", False)
    req = _req("1.2.3.4", client_host="10.0.0.1")
    assert get_client_ip(req) == "10.0.0.1"


def test_single_trusted_proxy_uses_rightmost_hop(monkeypatch):
    monkeypatch.setattr(settings, "behind_proxy", True)
    monkeypatch.setattr(settings, "trusted_proxy_count", 1)
    # Attacker prepends a spoofed value; the trusted proxy appends the real peer.
    req = _req("1.2.3.4, 198.51.100.7")
    assert get_client_ip(req) == "198.51.100.7"


def test_spoofed_leftmost_is_ignored(monkeypatch):
    monkeypatch.setattr(settings, "behind_proxy", True)
    monkeypatch.setattr(settings, "trusted_proxy_count", 1)
    # Rotating the leftmost spoof must not change the bucket — rightmost is fixed.
    assert get_client_ip(_req("evilA, 198.51.100.7")) == "198.51.100.7"
    assert get_client_ip(_req("evilB, 198.51.100.7")) == "198.51.100.7"


def test_two_trusted_proxies_uses_second_from_right(monkeypatch):
    monkeypatch.setattr(settings, "behind_proxy", True)
    monkeypatch.setattr(settings, "trusted_proxy_count", 2)
    # client, real-client-to-edge, edge-to-nginx
    req = _req("spoof, 198.51.100.7, 10.0.0.2")
    assert get_client_ip(req) == "198.51.100.7"


def test_short_chain_falls_back_to_peer(monkeypatch):
    monkeypatch.setattr(settings, "behind_proxy", True)
    monkeypatch.setattr(settings, "trusted_proxy_count", 2)
    # Only one hop present but two trusted expected → cannot trust XFF, use peer.
    req = _req("just-one", client_host="10.0.0.9")
    assert get_client_ip(req) == "10.0.0.9"


def test_a_header_sent_on_several_lines_is_read_as_one_list(monkeypatch):
    monkeypatch.setattr(settings, "behind_proxy", True)
    monkeypatch.setattr(settings, "trusted_proxy_count", 1)
    # The client sent the first line; the proxy added its own instead of appending.
    assert get_client_ip(_req("6.6.6.6", "198.51.100.7")) == "198.51.100.7"
    assert get_client_ip(_req("6.6.6.6, 7.7.7.7", "198.51.100.7")) == "198.51.100.7"


def test_an_entry_that_is_not_an_address_falls_back_to_the_peer(monkeypatch):
    monkeypatch.setattr(settings, "behind_proxy", True)
    monkeypatch.setattr(settings, "trusted_proxy_count", 1)
    for junk in ("unknown", "x" * 8000, "198.51.100.7 extra", "1.2.3.4.5"):
        assert get_client_ip(_req(junk, client_host="10.0.0.9")) == "10.0.0.9", junk


def test_an_address_is_normalised_and_its_port_dropped(monkeypatch):
    monkeypatch.setattr(settings, "behind_proxy", True)
    monkeypatch.setattr(settings, "trusted_proxy_count", 1)
    assert get_client_ip(_req("198.51.100.7:51234")) == "198.51.100.7"
    assert get_client_ip(_req("[2001:db8::1]:443")) == "2001:db8::1"
    assert get_client_ip(_req("2001:DB8:0:0:0:0:0:1")) == "2001:db8::1"
    assert get_client_ip(_req("fe80::1%" + "x" * 8000)) == "fe80::1"
