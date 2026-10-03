"""Dynamic CORS middleware — origins derived from active client apps."""

from urllib.parse import urlparse

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.cors import CORSMiddleware

from src.config import settings
from src.models.client_app import ClientApp
from src.models.service_app import ServiceApp

logger = structlog.get_logger()

_allowed_origins: set[str] = set()


def _extract_origin(uri: str) -> str | None:
    """Extract origin (scheme://host[:port]) from a URL."""
    parsed = urlparse(uri)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return None


async def refresh_origins(db: AsyncSession) -> None:
    """Rebuild allowed origins from active client apps, service apps, and static config."""
    global _allowed_origins
    origins: set[str] = set(settings.cors_origin_list)

    # Client app redirect URIs
    stmt = select(ClientApp.redirect_uris).where(ClientApp.is_active.is_(True))
    result = await db.execute(stmt)
    for (uris,) in result.all():
        for uri in uris:
            origin = _extract_origin(uri)
            if origin:
                origins.add(origin)

    # Service app allowed origins
    svc_stmt = select(ServiceApp.allowed_origins).where(ServiceApp.is_active.is_(True))
    svc_result = await db.execute(svc_stmt)
    for (svc_origins,) in svc_result.all():
        for origin in svc_origins or []:
            origins.add(origin)

    _allowed_origins = origins
    logger.info("app.cors.refreshed", category="app", count=len(origins))


class DynamicCORSMiddleware(CORSMiddleware):
    """CORS middleware with origins derived from active client apps."""

    def __init__(self, app):
        super().__init__(
            app,
            allow_origins=[],
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            # X-Authz-Token: the SDK's browser authz-mode sends it cross-origin
            # (member directory / share-dialog calls) — without it here the
            # preflight 400s before the request ever reaches the endpoint.
            allow_headers=[
                "Content-Type",
                "Authorization",
                "X-Requested-With",
                "X-Authz-Token",
            ],
            # Echoed by RequestContextMiddleware (which sits outside CORS); expose
            # it so cross-origin clients can read it for log correlation.
            expose_headers=["X-Request-ID"],
            allow_credentials=True,
            max_age=600,
        )

    def is_allowed_origin(self, origin: str) -> bool:
        return origin in _allowed_origins
