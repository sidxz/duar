import os
import time
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from src.api.admin_routes import router as admin_router
from src.api.client_log_routes import router as client_log_router
from src.api.org_admin_routes import router as org_admin_router
from src.api.auth_routes import router as auth_router
from src.api.authz_routes import idp_router as authz_idp_router
from src.api.authz_routes import router as authz_router
from src.api.realm_routes import router as realm_router
from src.api.group_routes import router as group_router
from src.api.internal_org_routes import router as internal_org_router
from src.api.permission_routes import router as permission_router
from src.api.role_routes import router as role_router
from src.api.user_routes import router as user_router
from src.api.workspace_routes import router as workspace_router
from slowapi.errors import RateLimitExceeded

from src.config import settings
from src.logging_config import configure_logging
from src.version import __version__
from src.middleware.rate_limit import (
    limiter,
    rate_limit_exceeded_handler,
)
from slowapi.middleware import SlowAPIASGIMiddleware
from src.middleware.access_log import AccessLogMiddleware
from src.middleware.cors import DynamicCORSMiddleware, refresh_origins
from src.middleware.request_context import RequestContextMiddleware
from src.middleware.security_headers import (
    MaxBodySizeMiddleware,
    SecurityHeadersMiddleware,
)

logger = structlog.get_logger()


async def _run_migrations():
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    from alembic import command
    from alembic.config import Config

    def _migrate():
        alembic_cfg = Config("alembic.ini")
        # Keep OUR logging: env.py's fileConfig() would otherwise wipe the handlers
        # configure_logging() just installed and drop the root level to WARN.
        alembic_cfg.attributes["configure_logger"] = False
        command.upgrade(alembic_cfg, "head")

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=1) as pool:
        await loop.run_in_executor(pool, _migrate)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    tier = getattr(app.state, "tier", "all")
    logger.info("app.startup", port=settings.service_port, tier=tier)
    # Only the migrator tiers touch the schema — two instances running
    # `alembic upgrade head` at once can race. public + all migrate; the internal
    # listener trusts public to have migrated (compose orders it after public's
    # healthcheck). CORS warm is likewise pointless where no CORS middleware mounts.
    if tier in ("public", "all"):
        await _run_migrations()
        logger.info("app.db.migrated")

        # Warm CORS origin cache from active client apps
        from src.database import engine as db_engine

        from sqlalchemy.ext.asyncio import AsyncSession

        async with AsyncSession(db_engine) as db:
            await refresh_origins(db)

    # Security checks — fail-closed in production, warn in dev
    _insecure_session = (
        settings.session_secret_key == "dev-only-change-me-in-production"
    )
    _insecure_cookie = not settings.cookie_secure

    # Redis connectivity and auth check
    _redis_down = False
    _redis_no_auth = False
    _redis_no_tls = False
    _redis_no_cert_verify = False
    try:
        from src.services.token_service import get_redis

        r = await get_redis()
        await r.ping()
        if "@" not in settings.redis_url:
            _redis_no_auth = True
        if not settings.redis_url.startswith("rediss://"):
            _redis_no_tls = True
        elif settings.redis_tls_verify != "required":
            _redis_no_cert_verify = True
    except Exception:
        _redis_down = True

    if not settings.debug:
        errors: list[tuple[str, str]] = []
        if _insecure_session:
            errors.append(
                (
                    "insecure_session",
                    "SESSION_SECRET_KEY is using the default dev value",
                )
            )
        if _insecure_cookie:
            errors.append(
                (
                    "insecure_cookie",
                    "COOKIE_SECURE is False — cookies will be sent over HTTP",
                )
            )
        if _redis_down:
            errors.append(
                (
                    "redis_down",
                    "Redis is unreachable — auth codes, refresh tokens, and denylist will fail",
                )
            )
        if _redis_no_auth:
            errors.append(
                (
                    "redis_no_auth",
                    "Redis URL has no authentication — set a password in REDIS_URL (redis://:password@host:port/db)",
                )
            )
        if _redis_no_tls:
            logger.warning("app.config.insecure", category="app", reason="redis_no_tls")
        if _redis_no_cert_verify:
            errors.append(
                (
                    "redis_no_cert_verify",
                    "REDIS_TLS_VERIFY is not 'required' — rediss:// accepts any certificate (MITM); set REDIS_TLS_VERIFY=required",
                )
            )
        if "*" in settings.allowed_hosts_list:
            errors.append(
                (
                    "allowed_hosts_wildcard",
                    "ALLOWED_HOSTS is wildcard — set explicit hosts via ALLOWED_HOSTS or BASE_URL/ADMIN_URL",
                )
            )
        if not settings.rate_limit_enabled:
            errors.append(
                (
                    "rate_limit_disabled",
                    "RATE_LIMIT_ENABLED is False — brute-force/DoS protection is off (intended only for ephemeral test/pentest targets)",
                )
            )
        if not settings.log_pii_redaction:
            errors.append(
                (
                    "pii_redaction_disabled",
                    "LOG_PII_REDACTION is False — raw emails/secrets would be written to the log stream",
                )
            )
        if errors:
            for code, detail in errors:
                logger.critical(
                    "app.config.insecure", category="app", reason=code, detail=detail
                )
            raise RuntimeError(
                "Refusing to start: insecure configuration with DEBUG=False. "
                f"Fix: {'; '.join(detail for _, detail in errors)}"
            )
    else:
        if _insecure_session:
            logger.warning(
                "app.config.insecure", category="app", reason="insecure_session_key"
            )
        if _insecure_cookie:
            logger.warning(
                "app.config.insecure", category="app", reason="insecure_cookie"
            )
        if _redis_down:
            logger.warning("app.config.insecure", category="app", reason="redis_down")
        if _redis_no_auth:
            logger.warning(
                "app.config.insecure", category="app", reason="redis_no_auth"
            )
        if _redis_no_tls:
            logger.warning("app.config.insecure", category="app", reason="redis_no_tls")

    app.state.start_time = time.time()
    yield
    logger.info("app.shutdown")


# Routers grouped by listener tier. public = browser/human surface; internal =
# the service-key-only surface (no socket on the public internet in a split deploy).
PUBLIC_ROUTERS = [
    admin_router,
    org_admin_router,
    auth_router,
    authz_idp_router,
    user_router,
    workspace_router,
    group_router,
    client_log_router,
]
INTERNAL_ROUTERS = [
    realm_router,
    permission_router,
    authz_router,
    role_router,
    internal_org_router,
]


def _resolve_tier() -> str:
    """Which listener this process is: 'public', 'internal', or 'all' (default).

    'all' is a single combined app = today's behavior (dev, make start, tests, and
    small single-process deployments). Production opts into the split by setting
    TIER=public on the published service and TIER=internal on the unpublished one.
    """
    tier = os.getenv("TIER", "all").strip().lower()
    if tier not in ("all", "public", "internal"):
        raise RuntimeError(f"TIER must be one of all|public|internal, got {tier!r}")
    return tier


class HealthExemptTrustedHostMiddleware(TrustedHostMiddleware):
    """TrustedHost check that lets /health through regardless of Host header."""

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/health":
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


def create_app(tier: str) -> FastAPI:
    """Build a listener for the given tier. Same image, different surface."""
    app = FastAPI(
        title=f"Duar ({tier})",
        description="Authentication, workspace management, and permissions",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        openapi_url="/openapi.json" if settings.debug else None,
    )
    # Stash the tier so the (shared, module-level) lifespan can gate migrations +
    # CORS warm. Set before startup runs, so the lifespan reads it via app.state.
    app.state.tier = tier

    # --- Middleware (last added = outermost, processes request first) ---

    # Reject oversized request bodies (10 MB)
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=10_485_760)

    # Rate limiting (slowapi). Deliberately positioned INSIDE
    # RequestContext/AccessLog/CORS (added after this) so a 429 still carries the
    # request-id, is access-logged, and gets CORS headers. See middleware/rate_limit.py.
    app.add_middleware(SlowAPIASGIMiddleware)

    # Security headers on every response
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.cookie_secure)

    # Session middleware is browser/OAuth-only — the internal listener has no human
    # callers, so it drops Session (and CORS below): less surface, and it cannot be
    # driven by a forged cookie/Origin.
    if tier in ("public", "all"):
        app.add_middleware(
            SessionMiddleware,
            secret_key=settings.session_secret_key,
            https_only=settings.cookie_secure,
            same_site="lax",
            max_age=600,  # 10 min — bounds the OAuth flow window
        )

    # Trusted host validation (prevents Host header attacks). /health is exempt:
    # k8s probes hit the pod IP directly, so their Host header can never be on
    # the allowlist — and the endpoint returns a static body, so the check adds
    # nothing there.
    if "*" not in settings.allowed_hosts_list:
        app.add_middleware(
            HealthExemptTrustedHostMiddleware,
            allowed_hosts=settings.allowed_hosts_list,
        )

    if tier in ("public", "all"):
        app.add_middleware(DynamicCORSMiddleware)
    app.add_middleware(AccessLogMiddleware)  # inside RequestContext
    app.add_middleware(RequestContextMiddleware)  # last added = outermost

    # Rate limiting state + handler
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    routers = []
    if tier in ("public", "all"):
        routers += PUBLIC_ROUTERS
    if tier in ("internal", "all"):
        routers += INTERNAL_ROUTERS
    for router in routers:
        app.include_router(router)

    @app.get("/health")
    @limiter.exempt  # health probes must never be throttled
    async def health():
        return {"status": "ok"}

    # JWKS is public by default — public keys are meant to be published. An
    # authz-only deployment (every verifier a backend) could move it internal.
    if tier in ("public", "all"):

        @app.get("/.well-known/jwks.json", tags=["auth"])
        async def jwks():
            from src.auth.jwks import build_jwks

            return build_jwks()

    return app


app = create_app(_resolve_tier())
