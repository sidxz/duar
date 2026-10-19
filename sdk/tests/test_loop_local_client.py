"""SDK HTTP clients keep working when reused across event loops.

A module-level client used once in ``asyncio.run(...)`` (e.g. registering actions)
and then inside a server's own loop must not fail its first request there. The
failure needs a real pooled keep-alive connection, so these tests talk to a local
HTTP/1.1 server instead of a mocked transport.
"""

import asyncio
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from duar_auth import AuthzClient, PermissionClient, RoleClient

RID = uuid.uuid4()

_RESPONSES = {
    "/roles/check-action": {"allowed": True, "roles": []},
    "/permissions/check": {
        "results": [
            {"service_name": "svc", "resource_type": "doc", "resource_id": str(RID), "action": "view", "allowed": True}
        ]
    },
    "/authz/resolve": {"user": {"id": "u"}},
}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive, so httpx pools the connection

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = json.dumps(_RESPONSES[self.path]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def duar_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_role_client_survives_two_event_loops(duar_url):
    rc = RoleClient(duar_url, "svc", "sk")
    assert asyncio.run(rc.check_action("t", "a", uuid.uuid4())) is True
    assert asyncio.run(rc.check_action("t", "a", uuid.uuid4())) is True


def test_permission_client_survives_two_event_loops(duar_url):
    pc = PermissionClient(duar_url, "svc", "sk")
    assert asyncio.run(pc.can("t", "doc", RID, "view")) is True
    assert asyncio.run(pc.can("t", "doc", RID, "view")) is True


def test_authz_client_survives_two_event_loops(duar_url):
    ac = AuthzClient(duar_url, "sk")
    assert asyncio.run(ac.resolve("idp", "google"))["user"] == {"id": "u"}
    assert asyncio.run(ac.resolve("idp", "google"))["user"] == {"id": "u"}


def test_same_loop_reuses_client():
    from duar_auth._utils import LoopLocalClient

    holder = LoopLocalClient(timeout=5.0)

    async def twice():
        return holder.get(), holder.get()

    first, second = asyncio.run(twice())
    assert first is second
