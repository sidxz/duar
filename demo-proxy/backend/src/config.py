from pydantic_settings import BaseSettings, SettingsConfigDict
from duar_auth import Duar


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    duar_url: str = "http://localhost:9003"
    service_name: str = "team-notes"
    service_api_key: str = ""
    allowed_workspaces: list[str] = []
    host: str = "0.0.0.0"
    port: int = 9100
    frontend_url: str = "http://localhost:9101"


settings = Settings()

duar = Duar(
    base_url=settings.duar_url,
    service_name=settings.service_name,
    service_key=settings.service_api_key,
    mode="proxy",
    actions=[
        {"action": "notes:export", "description": "Export notes as JSON"},
        {"action": "notes:bulk-delete", "description": "Bulk delete notes"},
    ],
    allowed_workspaces=set(settings.allowed_workspaces) or None,
)
