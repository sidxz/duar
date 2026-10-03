# service/tests/integration/gen_fixtures.py
"""Generate service/tests/integration/fixtures/fixtures.json from the REAL minters.

Standalone (NOT a pytest module). Uses an ephemeral in-memory RSA keypair written to
throwaway temp files, so no private key is ever committed — only the public JWKS +
signed tokens. Re-run via `make realm-fixtures` whenever the token shape changes.
"""

import json
import os
import tempfile
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

_ISSUER = "https://duar.test"

# 1. Ephemeral keypair → temp PEM files (discarded at process exit).
_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_priv_pem = _key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
_pub_pem = (
    _key.public_key()
    .public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    .decode()
)
_tmp = Path(tempfile.mkdtemp())
(_tmp / "priv.pem").write_text(_priv_pem)
(_tmp / "pub.pem").write_text(_pub_pem)

# 2. Point the service key seam at our ephemeral key BEFORE importing src.*.
os.environ["JWT_PRIVATE_KEY_PATH"] = str(_tmp / "priv.pem")
os.environ["JWT_PUBLIC_KEY_PATH"] = str(_tmp / "pub.pem")
os.environ["JWT_PREVIOUS_PUBLIC_KEY_PATHS"] = ""
os.environ["BASE_URL"] = _ISSUER
# Keep the committed authz vector long-lived (10y) like the m2m ones — prod default is
# 5 min, which makes a static committed token a time-bomb (expires 5 min after gen).
os.environ["AUTHZ_TOKEN_EXPIRE_MINUTES"] = str(10 * 365 * 24 * 60)

from src.auth import key_provider  # noqa: E402
from src.auth.jwks import build_jwks  # noqa: E402
from src.auth.jwt import create_authz_token, create_m2m_token  # noqa: E402

key_provider.reset_cache()  # ensure it reads our ephemeral key, not a cached one

_TEN_YEARS = 10 * 365 * 24 * 3600

tokens = {
    "m2m_valid": create_m2m_token(svc="acme-suite", caller="app-a", ttl_s=_TEN_YEARS),
    "m2m_expired": create_m2m_token(svc="acme-suite", caller="app-a", ttl_s=-10),
    "m2m_wrong_realm": create_m2m_token(
        svc="other-realm", caller="app-a", ttl_s=_TEN_YEARS
    ),
    "m2m_aud_target": create_m2m_token(
        svc="acme-suite", caller="app-a", ttl_s=_TEN_YEARS, aud_target="billing"
    ),
    "authz_valid": create_authz_token(
        user_id=uuid.UUID(int=1),
        idp_sub="google|1",
        workspace_id=uuid.UUID(int=2),
        workspace_slug="acme",
        workspace_role="editor",
        actions=["read"],
        service_name="acme-suite",
        org_id=None,
        org_slug=None,
        org_is_public=False,
    ),
}

fixtures = {
    "issuer": _ISSUER,
    "public_pem": _pub_pem,
    "jwks": build_jwks(),
    "tokens": tokens,
}

out = Path(__file__).parent / "fixtures" / "fixtures.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(fixtures, indent=2) + "\n")
print(f"wrote {out}")
