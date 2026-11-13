"""Rate limiting via slowapi (Redis-backed). slowapi is the SOLE mechanism.

Coverage model (slowapi 0.1.10 — verified against the installed source):
  * ``address_limits`` is an app-level dependency: it runs on every route once
    the router has picked it, ahead of the route's own dependencies, so ahead
    of authentication.
  * Routes WITHOUT a decorator → ``application_limits`` (per-IP aggregate,
    "global" scope) + ``default_limits`` (per-IP, per-route), checked there.
  * Routes WITH an ``@limiter.limit(...)`` decorator → that decorator's limit
    only, checked in the endpoint wrapper AFTER dependencies, when the caller
    is known.
  * A decorated route keyed per caller (user, service, app + user) has no
    caller to count a request against when authentication fails, so a 401
    there counts per IP instead: at the aggregate tier's rate, in a bucket of
    its own on each listener. Past that rate the answer is 429 in place of the
    401. Nothing is refused before authentication, so valid credentials always
    get through, whatever else has come from their address. A 401 for a caller
    that proved a service key is not counted: a backend relays its users'
    tokens from one address.
  * A route is one bucket per key (``key_style="endpoint"``): requests to
    ``/things/a`` and ``/things/b`` count together against ``/things/{id}``.
  * Never counted: a request the framework answers before any dependency runs
    (unknown path, wrong method, a body that does not parse), the static mount
    and, with DEBUG on, the docs pages. On a decorated route, a request that a
    dependency rejects with anything but 401, or whose input fails validation.

ACCEPTED TRADEOFF: app-layer limiting provides NO volumetric DoS protection.
The check runs after the router has read and parsed the request body, and a
decorated route counts its caller only after its dependencies ran: a request
that fails authentication there has had its credentials looked at, however
many came before it. Deploy EDGE rate limiting (nginx/Cloudflare/ALB) for
that. See docs/security.md.

Storage failure: a storage (Redis) error never 500s or refuses a request. The
request that meets the error is let through, and from then on this process
counts in memory (slowapi's ``in_memory_fallback_enabled``), looking for Redis
again at 1 to 32 second intervals. Real limit breaches still 429. The storage
client has socket timeouts (``_STORAGE_TIMEOUTS``), so a Redis which stops
answering is an error too, not a wait.

NOTE on storage-failure correctness: ``swallow_errors`` alone is NOT sufficient. When
slowapi swallows a storage error it never sets ``request.state.view_rate_limit``,
and its header-injection path (in the decorated-route wrapper) then
dereferences that attribute unconditionally — raising
``AttributeError`` → HTTP 500 on every request during a Redis outage. We close
that hole with ``_FailOpenLimiter`` below, which guarantees the attribute is
always defined so the (no-op, headers-disabled) injection can't crash.
"""

import ipaddress
import itertools
from collections.abc import AsyncIterator, Callable

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.wrappers import Limit
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.config import settings
from src.logging_events import log_security


def _as_ip(value: str) -> str | None:
    """``value`` as a normalised IP address, or None when it is not one.

    Takes the ``ip:port`` and ``[ip]:port`` forms some proxies write. An IPv6
    zone (``%eth0``) is dropped: it is free text, and not part of the address.
    """
    host = value
    if host.startswith("["):
        host = host[1:].partition("]")[0]
    elif host.count(":") == 1:
        host = host.partition(":")[0]
    try:
        return str(ipaddress.ip_address(host.partition("%")[0]))
    except ValueError:
        return None


def get_client_ip(request: Request) -> str:
    """Extract the client IP, trusting only the configured proxy hop.

    When behind a reverse proxy, the real client IP is the ``trusted_proxy_count``-th
    entry from the RIGHT of ``X-Forwarded-For`` — the hop our trusted proxy
    appended. The leftmost entries are client-controlled and must never be
    trusted, otherwise an attacker can rotate them to evade per-IP rate limits.
    A header sent on several lines is one list, in order: a proxy that adds a
    line of its own instead of appending is still the last entry. Falls back to
    the direct peer when the chain is shorter than expected or the entry is not
    an IP address, so what a client writes there never becomes a bucket key.
    """
    if settings.behind_proxy:
        forwarded = ",".join(request.headers.getlist("x-forwarded-for"))
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        idx = settings.trusted_proxy_count
        if 0 < idx <= len(parts):
            if ip := _as_ip(parts[-idx]):
                return ip
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


def service_address_key(request: Request) -> str:
    """Rate-limit key for POST /authz/resolve: bucket by (calling service, client address).

    A backend that calls directly is one address, so it is one bucket. Behind
    the SDK proxy helpers the address is the one the proxy chain reports for
    the browser. ``RATE_LIMIT_AUTHZ_RESOLVE_SERVICE`` caps a service across all
    addresses.
    """
    svc = getattr(request.state, "caller_service", None)
    ip = get_client_ip(request)
    return f"svc:{svc}|ip:{ip}" if svc else f"ip:{ip}"


def service_user_key(request: Request) -> str:
    """Rate-limit key for the permission-check hot path: bucket by (calling app, user).

    Per user so all users of one app (sharing its egress IP) don't share a bucket;
    per app too so one app's agent loop can't exhaust the user's checks in every
    other app. ``get_user_for_service_call`` binds both before the check fires.
    """
    svc = getattr(request.state, "caller_service", None)
    actor = getattr(request.state, "actor", None)
    if svc and actor:
        return f"svc:{svc}|user:{actor}"
    return user_or_ip_key(request)


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
            "endpoint": "failed authentication · per IP · all routes limited per caller",
            "limit": settings.rate_limit_aggregate or "disabled",
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
            "endpoint": "authz resolve · per service + IP",
            "limit": settings.rate_limit_authz_resolve,
        },
        {
            "endpoint": "authz resolve · per service · all addresses",
            "limit": settings.rate_limit_authz_resolve_service,
        },
        {
            "endpoint": "authenticated reads · per user",
            "limit": settings.rate_limit_read,
        },
        {
            "endpoint": "permission checks and lookups (check-action, permissions/check, accessible, share, user-actions) · per app+user",
            "limit": settings.rate_limit_tool_check,
        },
        {
            "endpoint": "service-key routes without a user (register, visibility, ACL, actions) · per service",
            "limit": settings.rate_limit_service,
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
    """Limiter that never 500s or refuses a request over a storage (Redis) error.

    While the storage is unreachable it counts in memory, per process.

    slowapi's ``swallow_errors`` swallows the storage exception during the limit
    check but leaves ``request.state.view_rate_limit`` unset; the subsequent
    header-injection step then dereferences it and raises ``AttributeError`` →
    HTTP 500. We pre-seed the attribute so that, on a swallowed storage error,
    injection sees ``None`` (a no-op given ``headers_enabled=False``) and the
    request passes through with its real response instead of 500ing.
    """

    def __init__(self, *args, **kwargs):
        # One bucket per route: every request to a route counts together,
        # whatever the values of its path parameters.
        kwargs.setdefault("key_style", "endpoint")
        # After a storage error, count in memory until the storage is back.
        kwargs.setdefault("in_memory_fallback_enabled", True)
        super().__init__(*args, **kwargs)

    def _check_request_limit(self, request, endpoint_func, in_middleware=True):
        try:
            request.state.view_rate_limit
        except AttributeError:
            request.state.view_rate_limit = None
        return super()._check_request_limit(request, endpoint_func, in_middleware)


# The storage client is synchronous and runs on the event loop: these bound how
# long one call to it can wait on Redis.
_STORAGE_TIMEOUTS = {"socket_timeout": 0.5, "socket_connect_timeout": 0.5}

limiter = _FailOpenLimiter(
    key_func=get_client_ip,
    default_limits=[settings.rate_limit_default] if settings.rate_limit_default else [],
    application_limits=(
        [settings.rate_limit_aggregate] if settings.rate_limit_aggregate else []
    ),
    storage_uri=settings.redis_url,
    storage_options={**settings.redis_ssl_kwargs, **_STORAGE_TIMEOUTS},
    swallow_errors=True,  # a Redis blip must not 500 or refuse legit traffic
    enabled=settings.rate_limit_enabled,  # master switch (False on ephemeral pentest targets)
)


class FailedAuthLimitExceeded(RateLimitExceeded):
    """An address is past its rate of failed authentication (see ``address_limits``)."""


def _service_key_proven(request: Request) -> bool:
    """Whether the caller presented a service key that validated.

    The auth dependencies set the flag where a key validates.
    ``caller_service`` does not say so: it is also bound from a matching
    ``Origin`` header, which any client can send.
    """
    return getattr(request.state, "service_key_proven", False)


def _storage_call(
    active: Limiter, call: Callable[..., bool], lim: Limit, *bucket: str
) -> bool:
    """``call`` (the storage's ``hit``) for one limit and bucket.

    A storage error counts as within the limit and, as inside slowapi, moves
    the limiter to counting in memory until its next check finds the storage
    back.
    """
    try:
        return call(lim.limit, *bucket)
    except Exception:
        if not active._swallow_errors:
            raise
        active.logger.exception("Failed to rate limit. Swallowing error")
        active._storage_dead = True
        return True


async def address_limits(request: Request) -> AsyncIterator[None]:
    """The per-address limits, applied before a route's own dependencies run.

    Installed app-wide: ``FastAPI(dependencies=[Depends(address_limits)])``.
    See the module docstring for which limit a route gets.
    """
    active: Limiter = request.app.state.limiter
    endpoint = request.scope["endpoint"]
    route = f"{endpoint.__module__}.{endpoint.__name__}"
    count_failures = False
    if active.enabled and route not in active._exempt_routes:
        own = active._route_limits.get(route)
        if own is None:
            active._check_request_limit(request, endpoint, True)
        else:
            count_failures = any(lim.key_func is not get_client_ip for lim in own)
    try:
        yield
    except HTTPException as exc:
        # A rejection by any later dependency, or by the handler, passes through
        # here. One hit both counts it and says whether it is one too many.
        if (
            count_failures
            and exc.status_code == 401
            and not _service_key_proven(request)
        ):
            # Per listener: the public and internal ones learn the client
            # address from different proxy chains.
            tier = getattr(request.app.state, "tier", "all")
            bucket = ("failed-auth", tier, get_client_ip(request))
            for lim in itertools.chain(*active._application_limits):
                if not _storage_call(active, active.limiter.hit, lim, *bucket):
                    raise FailedAuthLimitExceeded(lim) from exc
        raise


async def rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    from src.middleware.access_log import _route_template  # it imports this module

    # Tell the three apart so log-based alerting can separate volumetric abuse
    # (the per-IP aggregate: application_limits, "global" scope), an address
    # that keeps failing authentication, and one caller on a route's own tier.
    if isinstance(exc, FailedAuthLimitExceeded):
        reason = "failed_auth"
    elif getattr(exc.limit, "scope", "") == "global":
        reason = "aggregate"
    else:
        reason = "route_limit"
    log_security(
        "ratelimit.exceeded",
        outcome="denied",
        reason=reason,
        limit=str(exc.limit.limit),
        source_ip=get_client_ip(request),
        **{"http.route": _route_template(request.scope)},
    )
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests"},
        # slowapi's RateLimitExceeded has no .retry_after; source it from the
        # limit's window (e.g. 60s for any per-minute tier).
        headers={"Retry-After": str(exc.limit.limit.get_expiry())},
    )
