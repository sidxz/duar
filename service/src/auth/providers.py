from authlib.integrations.starlette_client import OAuth

from src.config import settings

oauth = OAuth()

# Google (OAuth2 + OpenID Connect)
if settings.google_client_id:
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
        code_challenge_method="S256",
    )

# GitHub (OAuth2 — not full OIDC, uses userinfo endpoint)
# Note: GitHub does not support PKCE as of 2025
if settings.github_client_id:
    oauth.register(
        name="github",
        client_id=settings.github_client_id,
        client_secret=settings.github_client_secret,
        access_token_url="https://github.com/login/oauth/access_token",
        authorize_url="https://github.com/login/oauth/authorize",
        api_base_url="https://api.github.com/",
        client_kwargs={"scope": "user:email"},
    )

# Microsoft EntraID (OAuth2 + OIDC)
if settings.entra_client_id and settings.entra_tenant_id:
    oauth.register(
        name="entra_id",
        client_id=settings.entra_client_id,
        client_secret=settings.entra_client_secret,
        server_metadata_url=(
            f"https://login.microsoftonline.com/{settings.entra_tenant_id}"
            "/v2.0/.well-known/openid-configuration"
        ),
        client_kwargs={"scope": "openid email profile"},
        code_challenge_method="S256",
    )

# Dex (self-hosted OIDC) — config-gated; inert unless DEX_CLIENT_ID +
# DEX_SERVER_METADATA_URL are set. Used by the Layer-2 isolation prover for faithful
# token issuance against an ephemeral target. Mirrors the OIDC providers above.
if settings.dex_client_id and settings.dex_server_metadata_url:
    oauth.register(
        name="dex",
        client_id=settings.dex_client_id,
        client_secret=settings.dex_client_secret,
        server_metadata_url=settings.dex_server_metadata_url,
        client_kwargs={"scope": "openid email profile"},
        code_challenge_method="S256",
    )


def get_configured_providers() -> list[str]:
    providers = []
    if settings.google_client_id:
        providers.append("google")
    if settings.github_client_id:
        providers.append("github")
    if settings.entra_client_id and settings.entra_tenant_id:
        providers.append("entra_id")
    if settings.dex_client_id and settings.dex_server_metadata_url:
        providers.append("dex")
    return providers
