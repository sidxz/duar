"""Rate limiting via slowapi (Redis-backed). slowapi is the SOLE mechanism.

Coverage model (slowapi 0.1.10 — verified against the installed source):
  * Routes WITHOUT a decorator → ``application_limits`` (per-IP aggregate,
    "global" scope) + ``default_limits`` (per-IP, per-route), enforced by
    ``SlowAPIASGIMiddleware`` BEFORE auth/dependencies run.
  * Routes WITH an ``@limiter.limit(...)`` decorator → ONLY that decorator's
    limit. slowapi intentionally EXEMPTS decorated routes from the middleware,
    so aggregate/default limits do NOT apply, and the decorator's check runs in
    the endpoint wrapper AFTER dependencies — a request rejected by an auth
    ``Depends`` (bad token/service key) is therefore not throttled here.

ACCEPTED TRADEOFF: app-layer limiting provides NO volumetric DoS protection and
NO pre-auth throttling for decorated routes (/authz/resolve, /auth/token, admin
POSTs). Deploy EDGE rate limiting (nginx/Cloudflare/ALB) for that. See
docs/security.md.

Fail-open: ``swallow_errors=True`` — a storage (Redis) error lets the request
through rather than 500ing or throttling. Real limit breaches still 429.

NOTE on fail-open correctness: ``swallow_errors`` alone is NOT sufficient. When
slowapi swallows a storage error it never sets ``request.state.view_rate_limit``,
and its header-injection path (both the ASGI middleware and the decorated-route
wrapper) then dereferences that attribute unconditionally — raising
``AttributeError`` → HTTP 500 on every request during a Redis outage. We close
that hole with ``_FailOpenLimiter`` below, which guarantees the attribute is
always defined so the (no-op, headers-disabled) injection can't crash.
"""

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.config import settings
from src.logging_events import log_security


def get_client_ip(request: Request) -> str:
    """Extract the client IP, trusting only the configured proxy hop.

    When behind a reverse proxy, the real client IP is the ``trusted_proxy_count``-th
    entry from the RIGHT of ``X-Forwarded-For`` — the hop our trusted proxy
    appended. The leftmost entries are client-controlled and must never be
    trusted, otherwise an attacker can rotate them to evade per-IP rate limits.
    Falls back to the direct peer when the chain is shorter than expected.
    """
    if settings.behind_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            parts = [p.strip() for p in forwarded.split(",") if p.strip()]
            idx = settings.trusted_proxy_count
            if parts and 0 < idx <= len(parts):
                return parts[-idx]
    if request.client:
        return request.client.host
    return "unknown"


def user_or_ip_key(request: Request) -> str:
    """Rate-limit key for authenticated endpoints: bucket by user, fall back to IP.

    ``bind_identity`` (request_context) writes the authenticated subject to
    ``request.state.actor`` during dependency resolution, which runs *before*
    slowapi's per-route check fires in the endpoint wrapper — so the actor is
    available here. IP fallback keeps the limit meaningful on any path where no
    user is resolved.
    """
    actor = getattr(request.state, "actor", None)
    if actor:
        return f"user:{actor}"
    return f"ip:{get_client_ip(request)}"


def service_or_ip_key(request: Request) -> str:
    """Rate-limit key for service-to-service endpoints: bucket by calling service.

    Used by POST /authz/resolve, where no user exists at dependency time (the IdP
    token is in the request body and the user is provisioned inside the handler).
    ``require_service_context`` binds ``caller_service`` before the check fires.
    """
    svc = getattr(request.state, "caller_service", None)
    if svc:
        return f"svc:{svc}"
    return f"ip:{get_client_ip(request)}"


def rate_limit_report() -> list[dict[str, str]]:
    """Live view of the active limit tiers for the admin Settings panel.

    Replaces the old hardcoded list, which had drifted from the real decorators.
    """
    return [
        {
            "endpoint": "aggregate · per IP · all undecorated routes",
            "limit": settings.rate_limit_aggregate or "disabled",
        },
        {
            "endpoint": "default · per IP · per undecorated route",
            "limit": settings.rate_limit_default or "disabled",
        },
        {
            "endpoint": "auth (login/callback/token/refresh) · per IP",
            "limit": settings.rate_limit_auth,
        },
        {
            "endpoint": "admin auth (login/callback) · per IP",
            "limit": settings.rate_limit_auth_admin,
        },
        {
            "endpoint": "authz resolve · per service",
            "limit": settings.rate_limit_authz_resolve,
        },
        {
            "endpoint": "authenticated reads · per user",
            "limit": settings.rate_limit_read,
        },
        {
            "endpoint": "admin writes · per user",
            "limit": settings.rate_limit_admin_write,
        },
        {
            "endpoint": "sensitive admin ops · per user",
            "limit": settings.rate_limit_sensitive,
        },
    ]


class _FailOpenLimiter(Limiter):
    """Limiter that truly fails open when the storage (Redis) is unreachable.

    slowapi's ``swallow_errors`` swallows the storage exception during the limit
    check but leaves ``request.state.view_rate_limit`` unset; the subsequent
    header-injection step then dereferences it and raises ``AttributeError`` →
    HTTP 500. We pre-seed the attribute so that, on a swallowed storage error,
    injection sees ``None`` (a no-op given ``headers_enabled=False``) and the
    request passes through with its real response instead of 500ing.
    """

    def _check_request_limit(self, request, endpoint_func, in_middleware=True):
        try:
            request.state.view_rate_limit
        except AttributeError:
            request.state.view_rate_limit = None
        return super()._check_request_limit(request, endpoint_func, in_middleware)


limiter = _FailOpenLimiter(
    key_func=get_client_ip,
    default_limits=[settings.rate_limit_default] if settings.rate_limit_default else [],
    application_limits=(
        [settings.rate_limit_aggregate] if settings.rate_limit_aggregate else []
    ),
    storage_uri=settings.redis_url,
    storage_options=settings.redis_ssl_kwargs,
    swallow_errors=True,  # fail OPEN: a Redis blip must not 500 or throttle legit traffic
    enabled=settings.rate_limit_enabled,  # master switch (False on ephemeral pentest targets)
)


async def rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    # Distinguish the per-IP volumetric aggregate (application_limits, "global"
    # scope) from per-route limits so log-based alerting can separate volumetric
    # abuse from a single actor hitting a strict route tier.
    reason = (
        "aggregate" if getattr(exc.limit, "scope", "") == "global" else "route_limit"
    )
    log_security(
        "ratelimit.exceeded",
        outcome="denied",
        reason=reason,
        limit=str(exc.limit.limit),
        source_ip=get_client_ip(request),
        **{"http.route": request.url.path},
    )
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests"},
        # slowapi's RateLimitExceeded has no .retry_after; source it from the
        # limit's window (e.g. 60s for any per-minute tier).
        headers={"Retry-After": str(exc.limit.limit.get_expiry())},
    )
