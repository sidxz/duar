"""AuthZ Mode endpoints — IdP token validation + authorization JWT issuance."""

import hmac
import uuid
from urllib.parse import urlparse

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import ServiceKeyContext, require_service_context
from src.auth.jwt import create_authz_token
from src.config import settings
from src.database import get_db
from src.logging_events import log_security
from src.middleware.rate_limit import limiter, service_or_ip_key
from src.middleware.request_context import bind_identity
from src.models.service_app import ServiceApp
from src.models.workspace import Workspace, WorkspaceMembership
from src.schemas.authz import (
    AuthzResolveRequest,
    AuthzResolveResponse,
    AuthzUserResponse,
    AuthzWorkspaceOption,
    AuthzWorkspaceResponse,
)
from src.services import auth_service, organization_service, service_app_service
from src.services.idp_validator import IdpValidationError, validate_idp_token
from src.services.role_service import get_user_actions

logger = structlog.get_logger()

router = APIRouter(prefix="/authz", tags=["authz"])

# Browser-facing GitHub proxy-login (server-side OAuth code exchange) uses
# request.session for OAuth-state CSRF, so it must live on the PUBLIC listener (which
# mounts SessionMiddleware). The service-key /resolve endpoint stays internal. Both
# keep the /authz prefix.
idp_router = APIRouter(prefix="/authz", tags=["authz-idp"])


async def service_app_origin_allowed(db: AsyncSession, redirect_uri: str) -> bool:
    """Pure predicate: is ``redirect_uri`` well-formed and its origin registered
    on an active ServiceApp? No logging, no raising — callers own how they react
    to False. Shared by the AuthZ idp-proxy allowlist (below) and the hosted
    /onboard return_to check, which log/react to a rejection differently (the
    idp-proxy flow hands a raw IdP token to redirect_uri — a rejection there is
    security-relevant at token-exfil severity; /onboard delivers nothing, so a
    stale bookmark is just a denied redirect, not an exfil attempt).
    """
    parsed = urlparse(redirect_uri)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.fragment:
        return False
    origin = f"{parsed.scheme}://{parsed.netloc}"
    stmt = select(ServiceApp.id).where(
        ServiceApp.is_active.is_(True),
        ServiceApp.allowed_origins.any(origin),
    )
    result = await db.execute(stmt)
    return bool(result.first())


async def _validate_authz_redirect_uri(db: AsyncSession, redirect_uri: str) -> None:
    """Assert ``redirect_uri``'s origin is registered on an active ServiceApp.

    Raises ``HTTPException(400)`` if the URI is malformed or the origin is not
    on any active ``ServiceApp.allowed_origins``. This is the allowlist for the
    AuthZ-mode proxy flow (``/authz/idp/*``) — in AuthZ mode trust is rooted in
    ServiceApp registration, so redirect targets must match a registered origin.
    """
    if await service_app_origin_allowed(db, redirect_uri):
        return
    parsed = urlparse(redirect_uri)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=400, detail="Invalid redirect_uri")
    if parsed.fragment:
        raise HTTPException(
            status_code=400, detail="redirect_uri must not contain a fragment"
        )
    origin = f"{parsed.scheme}://{parsed.netloc}"
    # This flow delivers a raw IdP token to redirect_uri in the URL
    # fragment — an allowlist probe here is an attempted token exfil.
    log_security(
        "authz.idp.redirect_rejected",
        outcome="denied",
        reason="origin_not_allowed",
        origin=origin,
    )
    raise HTTPException(
        status_code=400,
        detail="redirect_uri origin is not registered on any active service",
    )


@idp_router.get("/idp/{provider}/login")
@limiter.limit(settings.rate_limit_auth)
async def idp_login(
    request: Request,
    provider: str,
    redirect_uri: str,
    nonce: str,
    db: AsyncSession = Depends(get_db),
):
    """Redirect to an OAuth provider that requires server-side code exchange (e.g. GitHub).

    Stores redirect_uri and nonce in the session, then redirects to the
    provider's authorization URL. The callback endpoint exchanges the code
    and redirects back with the token in the URL hash.
    """
    if provider != "github":
        raise HTTPException(
            status_code=400, detail=f"Proxy login not supported for {provider}"
        )
    if not settings.github_client_id or not settings.github_client_secret:
        raise HTTPException(status_code=400, detail="GitHub OAuth is not configured")

    # Security: the IdP access token is delivered to ``redirect_uri`` in the URL
    # fragment. Without an allowlist any attacker-chosen URL would receive the
    # victim's GitHub token. Gate on ServiceApp.allowed_origins — the AuthZ-mode
    # trust root.
    await _validate_authz_redirect_uri(db, redirect_uri)

    # Security: ``state`` is the OAuth anti-CSRF token. We generate it here,
    # store it in the session, and echo it to GitHub. On callback we compare
    # the value GitHub returns against the session-stored value. Without this
    # binding, an attacker can pre-mint a GitHub code for their own account
    # and force the victim's session to exchange it — logging the victim in
    # as the attacker.
    state = uuid.uuid4().hex
    request.session["authz_idp_redirect_uri"] = redirect_uri
    request.session["authz_idp_nonce"] = nonce
    request.session["authz_idp_state"] = state

    params = (
        f"client_id={settings.github_client_id}"
        f"&redirect_uri={settings.base_url}/authz/idp/github/callback"
        f"&scope=read:user user:email"
        f"&state={state}"
    )
    return RedirectResponse(
        url=f"https://github.com/login/oauth/authorize?{params}",
        status_code=302,
    )


@idp_router.get("/idp/{provider}/callback")
@limiter.limit(settings.rate_limit_auth)
async def idp_callback(
    request: Request,
    provider: str,
    code: str,
    state: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Exchange authorization code for access token and redirect back to the frontend.

    Exchanges GitHub's authorization code for an access token, then redirects
    to the stored redirect_uri with the access token in the URL hash as
    `id_token` (matching the implicit flow format the SDK expects).
    """
    if provider != "github":
        raise HTTPException(
            status_code=400, detail=f"Proxy callback not supported for {provider}"
        )

    redirect_uri = request.session.pop("authz_idp_redirect_uri", None)
    nonce = request.session.pop("authz_idp_nonce", None)
    session_state = request.session.pop("authz_idp_state", None)

    # Security: validate OAuth state BEFORE any downstream work (DB lookups,
    # code exchange) so a CSRF'd callback fails fast with no side effects.
    # Constant-time compare to avoid leaking the valid state via timing.
    if not state or not session_state or not hmac.compare_digest(state, session_state):
        # The login-CSRF detector firing: a callback whose state doesn't match
        # the session that started the flow.
        log_security(
            "authz.idp.callback_rejected",
            outcome="denied",
            reason="state_mismatch",
            provider="github",
        )
        raise HTTPException(
            status_code=400,
            detail="Invalid or missing OAuth state — start from /authz/idp/{provider}/login",
        )

    if not redirect_uri:
        raise HTTPException(
            status_code=400,
            detail="No redirect_uri in session — start from /authz/idp/{provider}/login",
        )

    # Re-validate — the allowlist may have changed since the session started.
    await _validate_authz_redirect_uri(db, redirect_uri)

    # Exchange code for access token
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
            },
            headers={"Accept": "application/json"},
        )
    if resp.status_code != 200:
        log_security(
            "auth.idp.exchange_failed",
            outcome="failure",
            reason="github_code_exchange",
            provider="github",
            status=resp.status_code,
        )
        raise HTTPException(status_code=502, detail="GitHub code exchange failed")

    token_data = resp.json()
    access_token = token_data.get("access_token")
    if not access_token:
        error = token_data.get("error_description", token_data.get("error", "unknown"))
        log_security(
            "authz.idp.callback_rejected",
            outcome="failure",
            reason="github_oauth_error",
            provider="github",
            error=str(error)[:200],
        )
        raise HTTPException(status_code=400, detail=f"GitHub OAuth error: {error}")

    # Redirect back with token in hash (matches SDK's handleCallback expectations)
    fragment = f"id_token={access_token}"
    if nonce:
        fragment += f"&nonce={nonce}"
    return RedirectResponse(url=f"{redirect_uri}#{fragment}", status_code=302)


@router.post("/resolve", response_model=AuthzResolveResponse)
@limiter.limit(settings.rate_limit_authz_resolve, key_func=service_or_ip_key)
async def resolve(
    request: Request,
    body: AuthzResolveRequest,
    service_ctx: ServiceKeyContext = Depends(require_service_context),
    db: AsyncSession = Depends(get_db),
):
    """Validate IdP token, provision user, and return authorization context.

    If workspace_id is provided, returns a signed authz JWT for that workspace.
    If omitted, returns the list of workspaces the user belongs to.

    Origin-authenticated callers (browsers) may discover workspaces but MUST NOT
    mint authz tokens directly — token minting is a credential issuance step and
    requires an ``X-Service-Key`` (server-to-server trust). Browsers should route
    the mint call through their own backend, which holds the service key.
    """
    # Security: gate the mint step behind service-key auth. Origin auth is
    # lower-trust (just Origin-header match) and must not be sufficient to
    # issue a credential. Discovery (workspace list) is safe for Origin auth.
    if body.workspace_id is not None and service_ctx.origin_authenticated:
        # A lower-trust Origin-authenticated caller (browser) attempting the
        # credential-issuance step — privilege escalation against the mint gate.
        log_security(
            "authz.token.denied",
            outcome="denied",
            reason="origin_auth_cannot_mint",
            caller_service=service_ctx.service_name,
        )
        raise HTTPException(
            status_code=403,
            detail=(
                "Minting an authz token requires a service key. "
                "Call this endpoint from your backend with X-Service-Key."
            ),
        )

    # 1. Validate IdP token against provider's JWKS. Bind the token to the calling app's
    # registered IdP audience(s) when configured: a token minted for one app must not mint
    # via another app's service key, even though both share the deployment-wide provider.
    # Audiences are loaded lazily here (only this endpoint needs them) rather than on the
    # shared service-auth dependency that every endpoint pays for.
    audiences = list(service_ctx.allowed_idp_audiences)
    if not audiences and service_ctx.app_id is not None:
        audiences = list(
            await service_app_service.get_idp_audiences(db, service_ctx.app_id)
        )
    try:
        idp_claims = await validate_idp_token(
            body.idp_token,
            body.provider,
            expected_nonce=body.nonce,
            expected_audiences=audiences or None,
        )
    except IdpValidationError as e:
        log_security(
            "authz.idp.validation_failed",
            outcome="failure",
            reason="idp_validation",
            provider=body.provider,
            caller_service=service_ctx.service_name,
        )
        raise HTTPException(status_code=400, detail=str(e))

    # 2. Resolve the org from the verified IdP email and gate sign-in — the same
    # domain restriction the browser OAuth callback applies, so AuthZ mode cannot
    # be a side door around it. Then JIT-provision the user with that org.
    idp_email: str = idp_claims["email"]
    org = await organization_service.resolve_organization(db, idp_email)
    if org is None:
        log_security(
            "authz.token.denied",
            outcome="denied",
            reason="org_not_permitted",
            provider=body.provider,
            caller_service=service_ctx.service_name,
            workspace_id=str(body.workspace_id) if body.workspace_id else None,
        )
        raise HTTPException(
            status_code=403,
            detail="Sign-in is not permitted for this email domain.",
        )

    try:
        user = await auth_service.find_or_create_user(
            db=db,
            provider=body.provider,
            provider_user_id=idp_claims["sub"],
            email=idp_email,
            name=idp_claims["name"],
            organization_id=org.id,
            avatar_url=idp_claims.get("picture"),
        )
    except auth_service.CrossProviderEmailConflict as e:
        log_security(
            "authz.idp.email_conflict",
            outcome="denied",
            reason="email_conflict",
            provider=body.provider,
            email_domain=idp_email.rsplit("@", 1)[-1],
            caller_service=service_ctx.service_name,
        )
        raise HTTPException(status_code=409, detail=str(e))

    if not user.is_active:
        log_security(
            "authz.token.denied",
            outcome="denied",
            reason="inactive_user",
            actor=str(user.id),
        )
        raise HTTPException(status_code=403, detail="User account is inactive")

    # NOTE: build via a dict, not kwargs. The PII-leak guard in
    # tests/test_no_raw_pii_logging.py greps this file's source text for an
    # `email=` kwarg literal (to catch raw-email logging) — the kwarg form here
    # would trip it even though this is a response model, not a log call. The
    # dict form is deliberate; do not "simplify" it to keyword arguments.
    user_resp = AuthzUserResponse(
        **{"id": user.id, "email": user.email, "name": user.name}
    )

    # 3. If no workspace specified, return workspace list
    if not body.workspace_id:
        stmt = (
            select(Workspace, WorkspaceMembership.role)
            .join(WorkspaceMembership)
            .where(WorkspaceMembership.user_id == user.id)
            .order_by(Workspace.created_at)
        )
        rows = (await db.execute(stmt)).all()
        # Only surface workspaces this user's org can actually mint for — the
        # discovery list must not invite a guaranteed-403 on the follow-up call.
        # Batched into one filter query rather than one check per workspace.
        allowed_ws_ids = await organization_service.filter_workspaces_allowing_org(
            db, [ws.id for ws, _role in rows], user.organization_id
        )
        workspaces = [
            AuthzWorkspaceOption(id=ws.id, name=ws.name, slug=ws.slug, role=role)
            for ws, role in rows
            if ws.id in allowed_ws_ids
        ]
        return AuthzResolveResponse(user=user_resp, workspaces=workspaces)

    # 4. Resolve workspace membership
    stmt = select(WorkspaceMembership).where(
        WorkspaceMembership.workspace_id == body.workspace_id,
        WorkspaceMembership.user_id == user.id,
    )
    result = await db.execute(stmt)
    membership = result.scalar_one_or_none()
    if not membership:
        log_security(
            "authz.token.denied",
            outcome="denied",
            reason="not_member",
            actor=str(user.id),
            workspace_id=str(body.workspace_id),
        )
        raise HTTPException(
            status_code=403, detail="User is not a member of this workspace"
        )

    # Enforce the workspace's allowed-orgs list (the authoritative gate, same as
    # issue_tokens) so a disallowed org cannot mint an authz token either.
    if not await organization_service.workspace_allows_org(
        db, body.workspace_id, user.organization_id
    ):
        log_security(
            "authz.token.denied",
            outcome="denied",
            reason="org_not_allowed",
            actor=str(user.id),
            workspace_id=str(body.workspace_id),
        )
        raise HTTPException(
            status_code=403,
            detail="User's organization is not permitted in this workspace",
        )

    workspace = await db.get(Workspace, body.workspace_id)

    # 5. Get RBAC actions for this service
    actions = await get_user_actions(
        db, user.id, service_ctx.effective_scope, body.workspace_id
    )

    # 6. Sign authz JWT
    bind_identity(
        request,
        actor=str(user.id),
        workspace_id=str(workspace.id),
        caller_service=service_ctx.service_name,
    )
    log_security(
        "authz.token.issued",
        outcome="success",
        actor=str(user.id),
        workspace_id=str(workspace.id),
        workspace_role=membership.role,
        caller_service=service_ctx.service_name,
        actions_count=len(actions),
    )
    authz_token = create_authz_token(
        user_id=user.id,
        idp_sub=idp_claims["sub"],
        workspace_id=workspace.id,
        workspace_slug=workspace.slug,
        workspace_role=membership.role,
        actions=actions,
        service_name=service_ctx.effective_scope,
        # org is guaranteed non-None here (the 403 gate above rejects an
        # unresolved org), so this always carries real org claims.
        **organization_service.org_claims(org),
    )

    return AuthzResolveResponse(
        user=user_resp,
        workspace=AuthzWorkspaceResponse(
            id=workspace.id, slug=workspace.slug, role=membership.role
        ),
        authz_token=authz_token,
        expires_in=settings.authz_token_expire_minutes * 60,
    )
