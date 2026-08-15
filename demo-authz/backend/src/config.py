from pydantic_settings import BaseSettings, SettingsConfigDict

from duar_auth import Duar


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    duar_url: str = "http://localhost:9003"
    service_name: str = "team-notes"
    service_api_key: str = ""
    idp_jwks_url: str = "https://www.googleapis.com/oauth2/v3/certs"
    idp_audience: str = ""  # Google OAuth client_id this demo is registered as
    idp_issuer: str = "https://accounts.google.com"
    host: str = "0.0.0.0"
    port: int = 9200
    frontend_url: str = "http://localhost:9201"


settings = Settings()

if not settings.idp_audience:
    raise RuntimeError(
        "IDP_AUDIENCE is required — set it to this demo's Google OAuth client_id "
        "(e.g. 123-abc.apps.googleusercontent.com) in .env. Without it, any "
        "Google-signed token from any OAuth client would be accepted."
    )

duar = Duar(
    base_url=settings.duar_url,
    service_name=settings.service_name,
    service_key=settings.service_api_key,
    mode="authz",
    idp_jwks_url=settings.idp_jwks_url,
    idp_audience=settings.idp_audience,
    idp_issuer=settings.idp_issuer,
    actions=[
        {"action": "notes:export", "description": "Export notes as JSON"},
        {"action": "notes:bulk-delete", "description": "Bulk delete notes"},
    ],
)
