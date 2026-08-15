import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.jwt import _AUD_ACCESS, _AUD_ADMIN, _AUD_AUTHZ, decode_token
from src.database import get_db
from src.logging_events import log_security
from src.middleware.request_context import bind_identity


def _reject(
    status_code: int,
    detail: str,
    *,
    guard: str,
    reason: str,
    level: str | None = None,
    **fields,
) -> None:
    """Log-and-raise for auth-guard denials — every 401/403 from a dependency
    must leave a security-stream trace. Routine, high-volume rejects (missing
    or expired tokens) pass level="info"; sharp signals keep the default
    warning."""
    log_security(
        "auth.guard.rejected",
        outcome="denied",
        reason=reason,
        level=level,
        guard=guard,
        **fields,
    )
    raise HTTPException(status_code=status_code, detail=detail)


@dataclass(frozen=True)
class CurrentUser:
    user_id: uuid.UUID
    workspace_id: uuid.UUID
    workspace_role: str
    groups: list[uuid.UUID]


@dataclass(frozen=True)
class ServiceKeyContext:
    """Resolved service identity from X-Service-Key header or Origin."""

    service_name: str  # bound service name, or "" in dev mode
    origin_authenticated: bool = False  # True when resolved via Origin, not service key
    # The resolved app's id. /authz/resolve uses it to lazily load the app's
    # registered IdP audience(s) for per-app token binding — only that endpoint
    # needs them, so the lookup is deferred off the hot service-auth path.
    app_id: uuid.UUID | None = None
    # Pre-resolved IdP audience(s) (OIDC client_id(s)). Normally empty here and
    # loaded lazily from ``app_id`` in /authz/resolve; tests may set it directly.
    allowed_idp_audiences: tuple[str, ...] = ()
    # The member's realm slug, if this service belongs to a realm; else None.
    realm_slug: str | None = None

    @property
    def effective_scope(self) -> str:
        """Shared scope for permission + token binding: the realm slug for a member,
        else the service's own name (standalone — today's behavior)."""
        return self.realm_slug or self.service_name


def verify_service_scope(ctx: ServiceKeyContext, service_name: str) -> None:
    """Verify the service key is scoped to the requested service_name.

    For a realm member the authoritative scope is the realm slug (effective_scope),
    so all members share one permission namespace.
    """
    if ctx.effective_scope != service_name:
        _reject(
            403,
            f"Service key is not authorized for service '{service_name}'",
            guard="verify_service_scope",
            reason="service_scope_mismatch",
            caller_service=ctx.service_name,
            requested_service=service_name,
        )


async def require_service_context(
    request: Request, db: AsyncSession = Depends(get_db)
) -> ServiceKeyContext:
    """Resolve service identity from X-Service-Key header OR Origin header.

    Backends send X-Service-Key. Browser frontends are identified by
    matching the Origin header against ServiceApp.allowed_origins.
    """
    from src.services import service_app_service

    # 1. Try service key (backends)
    key = request.headers.get("X-Service-Key")
    if key:
        result = await service_app_service.validate_key(key, db)
        if not result:
            # Service-key brute force / leaked-key probing signal.
            _reject(
                401,
                "Invalid or missing service API key",
                guard="require_service_context",
                reason="invalid_service_key",
            )
        service_name, app_id, realm_slug = result
        bind_identity(request, caller_service=service_name)
        return ServiceKeyContext(
            service_name=service_name, app_id=app_id, realm_slug=realm_slug
        )

    # 2. Try origin (browser frontends) — lower trust than service key
    origin = request.headers.get("Origin")
    if origin:
        result = await service_app_service.validate_origin(origin, db)
        if result:
            service_name, app_id = result
            bind_identity(request, caller_service=service_name)
            return ServiceKeyContext(
                service_name=service_name,
                origin_authenticated=True,
                app_id=app_id,
            )

    _reject(
        401,
        "Missing service API key or unregistered origin",
        guard="require_service_context",
        reason="no_service_credentials",
        origin=origin,
    )


async def require_service_key(
    ctx: ServiceKeyContext = Depends(require_service_context),
) -> ServiceKeyContext:
    """FastAPI dependency: require service key authentication (not Origin).

    Wraps require_service_context but rejects Origin-based resolution.
    Use this for endpoints that need strict service-to-service auth.
    """
    # Security: Origin-based auth is lower trust — reject for service key-only endpoints
    if ctx.origin_authenticated:
        _reject(
            401,
            "Service key required",
            guard="require_service_key",
            reason="origin_auth_insufficient",
            caller_service=ctx.service_name,
        )
    return ctx


async def require_admin(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """FastAPI dependency that requires a valid admin JWT cookie."""
    token = request.cookies.get("admin_token")
    if not token:
        # info: the admin SPA probes /admin/me on every load before login.
        _reject(
            401,
            "Not authenticated",
            guard="require_admin",
            reason="missing_admin_cookie",
            level="info",
        )
    try:
        payload = decode_token(token, audience=_AUD_ADMIN)
    except Exception:
        _reject(
            401,
            "Invalid or expired token",
            guard="require_admin",
            reason="invalid_admin_token",
            level="info",
        )
    if not payload.get("admin"):
        _reject(
            403,
            "Not an admin",
            guard="require_admin",
            reason="not_admin",
            actor=payload.get("sub"),
        )

    from src.services.token_service import (
        is_access_token_blacklisted,
        is_user_deactivated,
    )

    # Check admin token revocation (jti denylist)
    if jti := payload.get("jti"):
        if await is_access_token_blacklisted(jti):
            # A revoked admin token still being presented — sharp signal.
            _reject(
                401,
                "Token has been revoked",
                guard="require_admin",
                reason="admin_token_revoked",
                actor=payload.get("sub"),
            )

    # Re-check user is active + still admin at request time. Flipping is_admin or
    # is_active must take effect immediately, not only after the cookie expires.
    user_id = payload.get("sub")
    if user_id:
        if await is_user_deactivated(user_id):
            _reject(
                401,
                "User account is deactivated",
                guard="require_admin",
                reason="user_deactivated",
                actor=user_id,
            )
        from src.models.user import User

        user = await db.get(User, uuid.UUID(user_id))
        if user is None or not user.is_active or not user.is_admin:
            # A de-admined/deactivated user still holding a valid cookie.
            _reject(
                401,
                "Admin privileges revoked",
                guard="require_admin",
                reason="admin_privileges_revoked",
                actor=user_id,
            )

    # CSRF: require X-Requested-With header on state-changing methods
    if request.method in ("POST", "PATCH", "PUT", "DELETE"):
        if not request.headers.get("X-Requested-With"):
            _reject(
                403,
                "Missing X-Requested-With header",
                guard="require_admin",
                reason="csrf_header_missing",
                actor=payload.get("sub"),
            )

    identity_kwargs: dict = {}
    if actor := payload.get("sub"):
        identity_kwargs["actor"] = actor
    if wid := payload.get("wid"):
        identity_kwargs["workspace_id"] = str(wid)
    if identity_kwargs:
        bind_identity(request, **identity_kwargs)

    return payload


async def get_current_user(request: Request) -> CurrentUser:
    """FastAPI dependency: extract user context from Bearer JWT."""
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        _reject(
            401,
            "Missing Bearer token",
            guard="get_current_user",
            reason="missing_token",
            level="info",
        )
    token = auth.removeprefix("Bearer ")
    if len(token) > 8192:
        _reject(
            401,
            "Token too large",
            guard="get_current_user",
            reason="oversized_token",
        )
    try:
        # Security: only accept access tokens — authz tokens must not be usable here
        payload = decode_token(token, audience=_AUD_ACCESS)
    except Exception:
        # info: expired access tokens are routine (SDKs refresh on 401);
        # brute force still shows as event-count, not level.
        _reject(
            401,
            "Invalid or expired token",
            guard="get_current_user",
            reason="invalid_token",
            level="info",
        )

    # Security: enforce token type to prevent cross-type confusion
    if payload.get("type") != "access":
        _reject(
            401,
            "Invalid token type",
            guard="get_current_user",
            reason="wrong_token_type",
            actor=payload.get("sub"),
        )

    # Security: reject tokens missing required claims
    if not all(k in payload for k in ("sub", "wid", "wrole")):
        _reject(
            401,
            "Token missing required claims",
            guard="get_current_user",
            reason="missing_claims",
            actor=payload.get("sub"),
        )

    await _enforce_token_hygiene(payload)

    current_user = CurrentUser(
        user_id=uuid.UUID(payload["sub"]),
        workspace_id=uuid.UUID(payload["wid"]),
        workspace_role=payload["wrole"],
        groups=[uuid.UUID(g) for g in payload.get("groups", [])],
    )
    bind_identity(
        request,
        actor=str(current_user.user_id),
        workspace_id=str(current_user.workspace_id),
    )
    return current_user


async def _enforce_token_hygiene(payload: dict) -> None:
    """Revocation + deactivation checks common to every user-bearing token.

    Applies to both access and authz tokens. Historically the authz-token path
    skipped these, leaving issued tokens valid until their TTL even after the
    user was deactivated — now fixed.
    """
    from src.services.token_service import (
        is_access_token_blacklisted,
        is_user_deactivated,
    )

    if jti := payload.get("jti"):
        if await is_access_token_blacklisted(jti):
            _reject(
                401,
                "Token has been revoked",
                guard="token_hygiene",
                reason="token_revoked",
                actor=payload.get("sub"),
            )
    if user_id := payload.get("sub"):
        if await is_user_deactivated(user_id):
            _reject(
                401,
                "User account is deactivated",
                guard="token_hygiene",
                reason="user_deactivated",
                actor=user_id,
            )


async def get_user_for_service_call(
    request: Request,
    svc_ctx: ServiceKeyContext = Depends(require_service_key),
) -> CurrentUser:
    """Extract user context from Bearer JWT — accepts access or authz tokens.

    Pair with dual-auth endpoints. In proxy mode, services forward the user's
    access token; in authz mode, services forward the authz token instead. The
    service key establishes trust; this extracts user identity and — for authz
    tokens — enforces the ``svc`` claim matches the calling service so a token
    minted for service A cannot be replayed on service B.
    """
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        _reject(
            401,
            "Missing Bearer token",
            guard="get_user_for_service_call",
            reason="missing_token",
            level="info",
            caller_service=svc_ctx.service_name,
        )
    token = auth.removeprefix("Bearer ")
    if len(token) > 8192:
        _reject(
            401,
            "Token too large",
            guard="get_user_for_service_call",
            reason="oversized_token",
            caller_service=svc_ctx.service_name,
        )
    try:
        payload = decode_token(token, audience=[_AUD_ACCESS, _AUD_AUTHZ])
    except Exception:
        _reject(
            401,
            "Invalid or expired token",
            guard="get_user_for_service_call",
            reason="invalid_token",
            level="info",
            caller_service=svc_ctx.service_name,
        )

    token_type = payload.get("type")
    if token_type not in ("access", "authz"):
        _reject(
            401,
            "Invalid token type",
            guard="get_user_for_service_call",
            reason="wrong_token_type",
            actor=payload.get("sub"),
            caller_service=svc_ctx.service_name,
        )

    # Security: reject tokens missing required claims
    if not all(k in payload for k in ("sub", "wid", "wrole")):
        _reject(
            401,
            "Token missing required claims",
            guard="get_user_for_service_call",
            reason="missing_claims",
            actor=payload.get("sub"),
            caller_service=svc_ctx.service_name,
        )

    await _enforce_token_hygiene(payload)

    if token_type == "authz":
        token_svc = payload.get("svc")
        if not token_svc or token_svc != svc_ctx.effective_scope:
            # A token minted for service A replayed against service B.
            _reject(
                403,
                "Authz token was issued for a different service",
                guard="get_user_for_service_call",
                reason="authz_service_mismatch",
                actor=payload.get("sub"),
                caller_service=svc_ctx.service_name,
                token_svc=token_svc,
            )

    bind_identity(
        request,
        actor=payload["sub"],
        workspace_id=payload["wid"],
        caller_service=svc_ctx.service_name,
    )
    return CurrentUser(
        user_id=uuid.UUID(payload["sub"]),
        workspace_id=uuid.UUID(payload["wid"]),
        workspace_role=payload["wrole"],
        groups=[uuid.UUID(g) for g in payload.get("groups", [])],
    )


async def get_current_user_flexible(
    request: Request, db: AsyncSession = Depends(get_db)
) -> CurrentUser:
    """Extract user context — accepts access tokens always, authz tokens only with valid service key.

    Use this on endpoints that need to work in both proxy mode (browser → access token)
    and authz mode (backend → service key + authz token).

    Security: authz tokens presented as the Bearer are only accepted when
    X-Service-Key is present AND validated against the database, with the key's
    effective scope equal to the token's ``svc`` claim. Additionally, a browser
    in authz mode (no service key — the SDK's DuarAuthz helpers) may present
    its Duar-minted authz token in ``X-Authz-Token``: that token is then the
    credential (Duar-signed, short-TTL, carries sub/wid/wrole/groups). Its
    ``svc`` claim binds it to a downstream service, not to Duar, so no scope
    comparison applies — the caller is the token's own subject.
    """
    # Validate the service key against the database — not just check for presence
    service_key_service_name: str | None = None
    service_key_effective_scope: str | None = None
    raw_key = request.headers.get("X-Service-Key")
    if raw_key:
        from src.services import service_app_service

        result = await service_app_service.validate_key(raw_key, db)
        if result is not None:
            service_key_service_name = result[0]
            service_key_effective_scope = result[2] or result[0]

    has_valid_service_key = service_key_service_name is not None
    # Browser authz-mode path: the Bearer slot carries the IdP token (which
    # Duar cannot re-validate here without per-app IdP context), so the
    # authz token rides in its own header and is what we authenticate.
    #
    # ACCEPTED RISK: the authz token is minted bound to a downstream service (its
    # `svc` claim); here we honor it as Duar-side identity without re-checking
    # `svc` or re-binding to the IdP token. Deliberately scoped — this dependency
    # guards only READ-only, workspace-scoped share-dialog endpoints (own profile
    # + the token's OWN workspace members/groups), data the subject is already
    # entitled to and that proxy mode already exposes to the browser. It cannot
    # reach writes/sharing (service-key gated) or /admin (admin cookie). Residual:
    # a captured authz token can read the user's own workspace directory at
    # Duar for its ~5-min TTL. Accepted over full IdP re-binding, which would
    # cost a per-request IdP re-verification (a GitHub API call per hit) for a
    # narrow, user-entitled surface. Still subject to the hygiene check below.
    browser_authz_token = (
        None if has_valid_service_key else request.headers.get("X-Authz-Token")
    )

    if browser_authz_token:
        token = browser_authz_token
        audiences: str | list[str] = _AUD_AUTHZ
        valid_types: tuple[str, ...] = ("authz",)
    else:
        auth = request.headers.get("Authorization")
        if not auth or not auth.startswith("Bearer "):
            _reject(
                401,
                "Missing Bearer token",
                guard="get_current_user_flexible",
                reason="missing_token",
                level="info",
            )
        token = auth.removeprefix("Bearer ")
        audiences = [_AUD_ACCESS, _AUD_AUTHZ] if has_valid_service_key else _AUD_ACCESS
        valid_types = ("access", "authz") if has_valid_service_key else ("access",)
    if len(token) > 8192:
        _reject(
            401,
            "Token too large",
            guard="get_current_user_flexible",
            reason="oversized_token",
        )

    try:
        payload = decode_token(token, audience=audiences)
    except Exception:
        _reject(
            401,
            "Invalid or expired token",
            guard="get_current_user_flexible",
            reason="invalid_token",
            level="info",
        )

    token_type = payload.get("type")
    if token_type not in valid_types:
        _reject(
            401,
            "Invalid token type",
            guard="get_current_user_flexible",
            reason="wrong_token_type",
            actor=payload.get("sub"),
        )

    if not all(k in payload for k in ("sub", "wid", "wrole")):
        _reject(
            401,
            "Token missing required claims",
            guard="get_current_user_flexible",
            reason="missing_claims",
            actor=payload.get("sub"),
        )

    await _enforce_token_hygiene(payload)

    if token_type == "authz" and has_valid_service_key:
        token_svc = payload.get("svc")
        if not token_svc or token_svc != service_key_effective_scope:
            _reject(
                403,
                "Authz token was issued for a different service",
                guard="get_current_user_flexible",
                reason="authz_service_mismatch",
                actor=payload.get("sub"),
                caller_service=service_key_service_name,
                token_svc=token_svc,
            )

    bind_identity(
        request,
        actor=payload["sub"],
        workspace_id=payload["wid"],
        caller_service=service_key_service_name,
    )
    return CurrentUser(
        user_id=uuid.UUID(payload["sub"]),
        workspace_id=uuid.UUID(payload["wid"]),
        workspace_role=payload["wrole"],
        groups=[uuid.UUID(g) for g in payload.get("groups", [])],
    )
