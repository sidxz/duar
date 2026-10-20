"""Redis over TLS with certificate verification: the `make setup` certs must pass
strict X.509 checks (Python 3.13+ default), and a Redis failure at startup must
report its cause instead of a bare "unreachable".
"""

import shutil
import socket
import ssl
import subprocess
import threading
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from src import main
from src.config import settings
from src.services import token_service

GEN_CERTS = Path(__file__).resolve().parents[2] / "scripts" / "gen-tls-certs.sh"


@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs the openssl CLI")
def test_setup_certs_pass_strict_verification(tmp_path):
    subprocess.run([str(GEN_CERTS), str(tmp_path)], check=True)  # noqa: S603

    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(tmp_path / "server.crt", tmp_path / "server.key")
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]

    def serve():
        conn, _ = listener.accept()
        try:
            server.wrap_socket(conn, server_side=True).close()
        except ssl.SSLError:
            pass

    threading.Thread(target=serve, daemon=True).start()

    # What redis-py builds for rediss:// with REDIS_TLS_VERIFY=required; strict is
    # forced so this fails on any Python, not just 3.13+ where it is the default.
    client = ssl.create_default_context(cafile=tmp_path / "ca.crt")
    client.verify_flags |= ssl.VERIFY_X509_STRICT
    client.minimum_version = ssl.TLSVersion.TLSv1_2
    with socket.create_connection(("127.0.0.1", port)) as raw:
        client.wrap_socket(raw, server_hostname="redis").close()
    listener.close()


@pytest.mark.asyncio
async def test_startup_reports_redis_failure_cause(monkeypatch):
    monkeypatch.setattr(settings, "debug", False)
    error = ssl.SSLCertVerificationError(
        "certificate verify failed: Missing Authority Key Identifier"
    )
    monkeypatch.setattr(token_service, "get_redis", AsyncMock(side_effect=error))

    app = FastAPI()
    app.state.tier = "internal"  # skips migrations and the DB-backed CORS warm-up
    with pytest.raises(RuntimeError) as raised:
        async with main.lifespan(app):
            pass

    message = str(raised.value)
    assert "Redis is unreachable" in message
    assert "SSLCertVerificationError" in message
    assert "Missing Authority Key Identifier" in message
