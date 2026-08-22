from pathlib import Path
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Database
    database_url: str = (
        "postgresql+asyncpg://identity:identity_dev@localhost:9001/identity?ssl=require"
    )

    # Redis
    redis_url: str = "rediss://:duar_dev@localhost:9002/0"
    redis_tls_ca_cert: str = ""  # Path to CA cert for Redis TLS (e.g. keys/tls/ca.crt)
    redis_tls_verify: str = "none"  # "none" | "required" — set "required" in production

    # JWT
    jwt_private_key_path: Path = Path("keys/private.pem")
    jwt_public_key_path: Path = Path("keys/public.pem")
    jwt_algorithm: str = "RS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
    admin_token_expire_minutes: int = 60
    authz_token_expire_minutes: int = 5
    jwt_previous_public_key_paths: str = (
        ""  # comma-separated retired public key paths (verify-only)
    )

    # OAuth2 providers
    google_client_id: str = ""
    google_client_secret: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    entra_client_id: str = ""
    entra_client_secret: str = ""
    entra_tenant_id: str = ""
    # Dex (self-hosted OIDC) — config-gated; consumed by the Layer-2 isolation prover.
    dex_client_id: str = ""
    dex_client_secret: str = ""
    dex_server_metadata_url: str = ""

    # Service
    service_host: str = "0.0.0.0"
    service_port: int = 9003
    base_url: str = "http://localhost:9003"
    frontend_url: str = "http://localhost:3000"

    # Session (generate with: python -c "import secrets; print(secrets.token_urlsafe(32))")
    session_secret_key: str = "dev-only-change-me-in-production"

    # CORS (comma-separated in .env)
    cors_origins: str = "http://localhost:3000,http://localhost:9101"

    # Security
    cookie_secure: bool = False  # Set True in production (requires HTTPS)
    allowed_hosts: str = ""  # comma-separated override; empty = derived from BASE_URL
    debug: bool = False  # Set True for local development (enables /docs, /redoc)
    rate_limit_enabled: bool = (
        True  # master switch; False only on ephemeral test targets
    )
    behind_proxy: bool = (
        False  # Set True when behind a reverse proxy (nginx, ALB, etc.)
    )
    trusted_proxy_count: int = (
        1  # Number of trusted reverse proxies between Duar and the internet.
        # The client IP is read from the Nth-from-right X-Forwarded-For entry, so
        # client-controlled (leftmost) values cannot spoof the rate-limit bucket.
    )

    # Rate-limit tiers — limits-library strings ("10/minute"). Only the two
    # middleware-level tiers below (default, aggregate) accept "" to disable them.
    # The per-route (decorated) tiers must be non-empty: "" there means
    # @limiter.limit("") = NO throttle AND exemption from the aggregate, so it is
    # rejected at startup (see _validate_decorated_tier). Read at import time
    # (restart to change). See middleware/rate_limit.py for the slowapi coverage
    # model and docs/security.md for the edge-rate-limiting caveat.
    rate_limit_default: str = "120/minute"  # per IP, per undecorated route (long tail)
    rate_limit_aggregate: str = (
        "300/minute"  # per IP across all undecorated routes (volumetric ceiling)
    )
    rate_limit_auth: str = (
        "10/minute"  # login/callback/token/refresh + authz idp (per IP)
    )
    rate_limit_auth_admin: str = "5/minute"  # admin login/callback (per IP)
    rate_limit_authz_resolve: str = (
        "60/minute"  # POST /authz/resolve (per calling service)
    )
    rate_limit_read: str = "60/minute"  # authenticated reads (per user)
    rate_limit_admin_write: str = "10/minute"  # admin mutations (per user)
    rate_limit_sensitive: str = "5/minute"  # destructive/expensive admin ops (per user)

    @field_validator("base_url", "admin_url", "frontend_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        # Every URL built from these is f"{base}/path" — a trailing slash would double it.
        return v.rstrip("/")

    @field_validator(
        "rate_limit_auth",
        "rate_limit_auth_admin",
        "rate_limit_authz_resolve",
        "rate_limit_read",
        "rate_limit_admin_write",
        "rate_limit_sensitive",
    )
    @classmethod
    def _validate_decorated_tier(cls, v: str, info) -> str:
        """Per-route tiers must be valid, non-empty limit strings.

        Unlike default/aggregate, an empty per-route tier is a silent security
        footgun (@limiter.limit("") = unlimited + exempt from the aggregate), so
        fail fast at startup rather than ship an unthrottled auth/admin route.
        """
        from limits import parse

        if not v:
            raise ValueError(
                f"{info.field_name} must be a non-empty limit string "
                f'(e.g. "10/minute"); "" is not allowed for a per-route tier'
            )
        parse(v)  # raises ValueError on a malformed limit string
        return v

    # Tier-1 security signals (detect-only; see docs/ai-security-roadmap.md)
    signals_enabled: bool = True
    signal_impossible_travel_kmh: int = 900  # implied speed above this → signal
    signal_stuffing_window_minutes: int = 15  # sliding window for failure counters
    signal_stuffing_failures: int = 10  # failures per IP in window, AND:
    signal_stuffing_distinct_emails: int = 5  # distinct emails per IP in window

    # Admin
    admin_emails: str = ""
    admin_url: str = "http://localhost:9004"

    # Logging
    log_level: str = "INFO"  # DEBUG|INFO|WARNING|ERROR|CRITICAL
    log_format: str = "json"  # "json" (prod) | "console" (dev)
    log_pii_redaction: bool = True  # mask emails/secrets in logs (always on in prod)

    @property
    def redis_ssl_kwargs(self) -> dict:
        """Extra kwargs for redis.from_url() when using rediss:// scheme."""
        if not self.redis_url.startswith("rediss://"):
            return {}
        import ssl as _ssl

        kwargs: dict = {}
        if self.redis_tls_ca_cert:
            kwargs["ssl_ca_certs"] = self.redis_tls_ca_cert
        kwargs["ssl_cert_reqs"] = (
            _ssl.CERT_REQUIRED
            if self.redis_tls_verify == "required"
            else _ssl.CERT_NONE
        )
        return kwargs

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def jwt_previous_public_key_paths_list(self) -> list[str]:
        if not self.jwt_previous_public_key_paths:
            return []
        return [
            p.strip()
            for p in self.jwt_previous_public_key_paths.split(",")
            if p.strip()
        ]

    @property
    def admin_email_list(self) -> list[str]:
        if not self.admin_emails:
            return []
        return [e.strip() for e in self.admin_emails.split(",") if e.strip()]

    @property
    def environment(self) -> str:
        """Coarse env label for log lines; derived from DEBUG."""
        return "dev" if self.debug else "prod"

    @property
    def allowed_hosts_list(self) -> list[str]:
        if self.allowed_hosts:
            return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]
        # Derive from BASE_URL + ADMIN_URL
        hosts = set()
        for url in [self.base_url, self.admin_url]:
            parsed = urlparse(url)
            if parsed.hostname:
                hosts.add(parsed.hostname)
        if not hosts:
            # No hosts derived — allow all in dev, but startup check
            # will reject this in production (DEBUG=False)
            return ["*"]
        return list(hosts)


settings = Settings()
