import html
import uuid
from urllib.parse import urlencode

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from src.api.dependencies import CurrentUser, get_current_user, require_admin
from src.auth.jwt import create_admin_token, decode_token
from src.auth.providers import get_configured_providers, oauth
from src.config import settings
from src.database import get_db
from src.models.client_app import ClientApp
from src.models.user import SocialAccount, User
from src.models.workspace import Workspace, WorkspaceMembership
from src.schemas.auth import (
    ProviderListResponse,
    RefreshRequest,
    SelectWorkspaceRequest,
    TokenResponse,
    WorkspaceListRequest,
    WorkspaceOptionResponse,
)
from src.logging_events import log_security
from src.middleware.rate_limit import get_client_ip, limiter
from src.services import (
    activity_service,
    auth_code_service,
    auth_service,
    organization_service,
    signal_service,
    token_service,
    workspace_service,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/auth", tags=["auth"])


def _error_page(status_code: int, title: str, message: str) -> HTMLResponse:
    # Base64-encoded splash.png is too large — use an inline SVG shield instead.
    # The response overrides the global CSP to allow inline styles and the SVG.
    safe_title = html.escape(title)
    safe_message = html.escape(message)
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title} — Duar</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ min-height: 100vh; display: flex; align-items: center; justify-content: center;
         background: #09090b; color: #e4e4e7; font-family: system-ui, -apple-system, sans-serif; }}
  .card {{ max-width: 420px; width: 100%; border: 1px solid #27272a;
           border-radius: 0.75rem; background: #18181b; overflow: hidden; }}
  .header {{ background: #f43737; padding: 1.5rem; text-align: center; }}
  .shield {{ width: 40px; height: 40px; margin: 0 auto 0.5rem; }}
  .brand {{ font-size: 0.625rem; font-weight: 700; letter-spacing: 0.15em;
            text-transform: uppercase; color: rgba(255,255,255,0.85); }}
  .body {{ padding: 2rem 2rem 1.75rem; text-align: center; }}
  h1 {{ font-size: 1.125rem; font-weight: 600; margin-bottom: 0.75rem; }}
  p {{ font-size: 0.875rem; color: #a1a1aa; line-height: 1.6; }}
  .meta {{ font-size: 0.75rem; color: #3f3f46; margin-top: 1.5rem;
           padding-top: 1rem; border-top: 1px solid #27272a; }}
</style>
</head>
<body>
  <div class="card">
    <div class="header">
      <svg class="shield" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M12 2L3 7v5c0 5.25 3.75 10.15 9 11.25C17.25 22.15 21 17.25 21 12V7l-9-5z"
              fill="rgba(0,0,0,0.2)" stroke="white" stroke-width="1.5" stroke-linejoin="round"/>
        <rect x="10" y="9" width="4" height="5" rx="0.5" fill="white" opacity="0.9"/>
        <circle cx="12" cy="8.5" r="2" fill="none" stroke="white" stroke-width="1.5" opacity="0.9"/>
      </svg>
      <div class="brand">Duar</div>
    </div>
    <div class="body">
      <h1>{safe_title}</h1>
      <p>{safe_message}</p>
      <div class="meta">Error {status_code}</div>
    </div>
  </div>
</body>
</html>"""
    resp = HTMLResponse(content=page, status_code=status_code)
    resp.headers["X-CSP-Override"] = "html-page"
    return resp


async def _log_login_failure(
    db: AsyncSession,
    request: Request,
    provider: str,
    reason: str,
    flow: str = "user",
    email: str | None = None,
    error_type: str | None = None,
    count_for_stuffing: bool = True,
    stream_event: str = "auth.login.failed",
    **stream_extra: str,
) -> None:
    """Best-effort admin-visible audit row for a failed sign-in.

    Also the single emit point for the login-failure security-stream event —
    every flow (user, admin, and pre-callback start rejects) routes through
    here, so the stream and the DB row can't drift apart. Start rejects pass
    ``stream_event="auth.login.rejected"`` (outcome "denied"); everything else
    keeps the default ``auth.login.failed`` (outcome "failure"). Any extra
    kwargs (e.g. ``client_id``, ``redirect_uri``) flow to both the stream
    fields and the DB detail dict.

    Rolls back first so the audit row is the ONLY thing committed (a failed
    flow may have flushed partial state, e.g. a half-linked user). Must never
    mask the original error path.
    """
    stream_fields: dict = {
        "provider": provider,
        "flow": flow,
        "source_ip": get_client_ip(request),
        **stream_extra,
    }
    if email and "@" in email:
        stream_fields["email_domain"] = email.split("@", 1)[-1]
    if error_type:
        stream_fields["error_type"] = error_type
    outcome = "denied" if stream_event == "auth.login.rejected" else "failure"
    log_security(stream_event, outcome=outcome, reason=reason, **stream_fields)
    try:
        await db.rollback()
        detail: dict = {
            "provider": provider,
            "reason": reason,
            "ip": get_client_ip(request),
            "user_agent": request.headers.get("user-agent", "")[:200],
        }
        if email:
            detail["email"] = email
        if error_type:
            detail["error_type"] = error_type
        detail.update(stream_extra)
        await activity_service.log_activity(
            db,
            action="admin_login_failed" if flow == "admin" else "login_failed",
            target_type="system",
            target_id=uuid.UUID(int=0),
            detail=detail,
        )
        await db.commit()
        if count_for_stuffing:
            await signal_service.on_login_failure(
                db,
                ip=detail["ip"],
                user_agent=detail["user_agent"],
                email=email,
            )
    except Exception:
        logger.warning(
            "audit.write_failed",
            category="app",
            reason="login_failure_audit_row",
            exc_info=True,
        )


@router.get("/providers", response_model=ProviderListResponse)
async def list_providers():
    return ProviderListResponse(providers=get_configured_providers())


@router.get("/login/{provider}")
@limiter.limit(settings.rate_limit_auth)
async def login(
    provider: str,
    request: Request,
    client_id: uuid.UUID = Query(..., description="ClientApp id this login is for"),
    redirect_uri: str = Query(...),
    code_challenge: str = Query(...),
    code_challenge_method: str = Query("S256"),
    state: str | None = Query(None, max_length=512),
    db: AsyncSession = Depends(get_db),
):
    configured = get_configured_providers()
    if provider not in configured:
        await _log_login_failure(
            db,
            request,
            provider,
            "provider_not_configured",
            count_for_stuffing=False,
            stream_event="auth.login.rejected",
        )
        return _error_page(
            400,
            "Provider Not Available",
            f"The login provider \u201c{provider}\u201d is not configured on this server.",
        )

    if code_challenge_method != "S256":
        await _log_login_failure(
            db,
            request,
            provider,
            "pkce_method_rejected",
            count_for_stuffing=False,
            stream_event="auth.login.rejected",
        )
        return _error_page(
            400,
            "Unsupported Challenge Method",
            "Only S256 code_challenge_method is supported.",
        )

    # Security: require redirect_uri to belong to the SPECIFIC ClientApp named
    # by client_id — not just any active ClientApp. Otherwise an attacker can
    # craft a login URL naming app B's redirect while controlling the PKCE
    # challenge, then redeem the resulting code against their own verifier
    # (Vuln 7 / login CSRF + auth-code theft).
    stmt = select(ClientApp).where(
        ClientApp.id == client_id,
        ClientApp.is_active.is_(True),
        ClientApp.redirect_uris.any(redirect_uri),
    )
    result = await db.execute(stmt)
    client_app = result.scalar_one_or_none()
    if not client_app:
        # Probe signal: unknown client_id or unregistered redirect_uri —
        # indefinitely repeatable enumeration attempts must leave a trace.
        await _log_login_failure(
            db,
            request,
            provider,
            "redirect_uri_not_allowed",
            count_for_stuffing=False,
            stream_event="auth.login.rejected",
            client_id=str(client_id),
            redirect_uri=redirect_uri,
        )
        return _error_page(
            400,
            "App Not Allowed",
            "The redirect URI is not registered for this client app. Check that the app is registered and enabled in the admin panel.",
        )

    # Store in session for callback (survives the OAuth round-trip). client_app_id
    # is persisted so the callback can re-verify no tampering has occurred.
    request.session["redirect_uri"] = redirect_uri
    request.session["code_challenge"] = code_challenge
    request.session["code_challenge_method"] = code_challenge_method
    request.session["client_app_id"] = str(client_app.id)
    # SPA-supplied CSRF state — opaque to Duar, echoed back verbatim on the
    # final redirect so the SPA can verify its own round-trip.
    if state:
        request.session["spa_state"] = state

    client = oauth.create_client(provider)
    oauth_redirect_uri = f"{settings.base_url}/auth/callback/{provider}"
    return await client.authorize_redirect(request, oauth_redirect_uri)


@router.get("/callback/{provider}")
@limiter.limit(settings.rate_limit_auth)
async def callback(
    provider: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    try:
        configured = get_configured_providers()
        if provider not in configured:
            return _error_page(
                400,
                "Provider Not Available",
                f"The login provider \u201c{provider}\u201d is not configured on this server.",
            )

        client = oauth.create_client(provider)
        token = await client.authorize_access_token(request)

        # Extract user info based on provider
        if provider == "github":
            resp = await client.get("user", token=token)
            profile = resp.json()
            # Always validate email via /user/emails (profile email may be unverified)
            resp = await client.get("user/emails", token=token)
            emails = resp.json()
            primary = next(
                (e for e in emails if e.get("primary") and e.get("verified")),
                None,
            )
            if not primary:
                await _log_login_failure(db, request, provider, "email_not_verified")
                return _error_page(
                    403,
                    "Email Not Verified",
                    "Your GitHub account does not have a verified primary email. "
                    "Please verify your email on GitHub and try again.",
                )
            profile["email"] = primary["email"]
            provider_user_id = str(profile["id"])
            email = profile["email"]
            name = profile.get("name") or profile.get("login", "")
            avatar_url = profile.get("avatar_url")
        else:
            # OIDC providers (Google, EntraID) — parse ID token
            userinfo = token.get("userinfo", {})
            if not auth_service.is_email_verified_claim(userinfo, provider):
                await _log_login_failure(db, request, provider, "email_not_verified")
                return _error_page(
                    403,
                    "Email Not Verified",
                    "Your identity provider did not confirm your email address. "
                    "Please verify your email and try again.",
                )
            provider_user_id = userinfo.get("sub", "")
            email = auth_service.extract_email_claim(userinfo)
            name = userinfo.get("name", "")
            avatar_url = userinfo.get("picture")
            profile = dict(userinfo)
            if not email:
                # Misconfigured app registration, not a credential attack —
                # keep it out of the stuffing counter.
                await _log_login_failure(
                    db, request, provider, "no_email_claim", count_for_stuffing=False
                )
                return _error_page(
                    403,
                    "No Email Address",
                    "Your identity provider did not return an email address. "
                    "Ask your administrator to add the 'email' optional claim to "
                    "the application registration.",
                )

        org = await organization_service.resolve_organization(db, email)
        if org is None:
            await _log_login_failure(
                db, request, provider, "org_not_permitted", email=email
            )
            return _error_page(
                403,
                "Sign-In Not Permitted",
                "Your email domain is not associated with an organization on "
                "this server, and public sign-in is disabled. Contact your "
                "administrator.",
            )

        try:
            user = await auth_service.find_or_create_user(
                db=db,
                provider=provider,
                provider_user_id=provider_user_id,
                email=email,
                name=name,
                organization_id=org.id,
                avatar_url=avatar_url,
                provider_data=profile,
            )
        except auth_service.CrossProviderEmailConflict:
            await _log_login_failure(
                db, request, provider, "cross_provider_conflict", email=email
            )
            return _error_page(
                409,
                "Email Already Used",
                "An account with this email address already exists under a "
                "different sign-in provider. Please sign in with your original "
                "provider, or contact your administrator to link accounts.",
            )

        await activity_service.log_activity(
            db,
            action="user_login",
            target_type="user",
            target_id=user.id,
            actor_id=user.id,
            detail={
                "provider": provider,
                "ip": get_client_ip(request),
                "user_agent": request.headers.get("user-agent", "")[:200],
            },
        )
        await db.commit()
        await signal_service.on_login_success(
            db,
            user_id=user.id,
            ip=get_client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:200],
        )

        # Retrieve redirect_uri, PKCE challenge, and client binding from session
        redirect_uri = request.session.pop("redirect_uri", None)
        code_challenge = request.session.pop("code_challenge", None)
        code_challenge_method = request.session.pop("code_challenge_method", None)
        session_client_app_id = request.session.pop("client_app_id", None)
        spa_state = request.session.pop("spa_state", None)
        request.session.clear()
        if not redirect_uri or not session_client_app_id:
            return _error_page(
                400,
                "Session Expired",
                "Your login session has expired. Please go back and try again.",
            )

        try:
            session_client_uuid = uuid.UUID(session_client_app_id)
        except (TypeError, ValueError):
            return _error_page(400, "Invalid Session", "Please sign in again.")

        # Re-validate the exact ClientApp that initiated login still owns this
        # redirect_uri and is still active. Binding (client_app, redirect_uri)
        # defends against redirect_uri substitution across apps.
        stmt = select(ClientApp).where(
            ClientApp.id == session_client_uuid,
            ClientApp.is_active.is_(True),
            ClientApp.redirect_uris.any(redirect_uri),
        )
        result = await db.execute(stmt)
        client_app = result.scalar_one_or_none()
        if not client_app:
            # redirect_uri-substitution detector: the (client_app, redirect_uri)
            # binding that passed at login start no longer holds at callback.
            log_security(
                "auth.login.rejected",
                outcome="denied",
                reason="app_not_allowed_at_callback",
                provider=provider,
                client_app_id=session_client_app_id,
                actor=str(user.id),
            )
            return _error_page(
                400,
                "App Not Allowed",
                "The app you are trying to sign into has been disabled or its configuration has changed. Contact your administrator.",
            )

        # Generate auth code and redirect (with PKCE challenge bound to code)
        code = await auth_code_service.create_auth_code(
            user.id,
            provider=provider,
            client_app_id=client_app.id,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
        )
        log_security(
            "auth.login.succeeded",
            outcome="success",
            provider=provider,
            actor=str(user.id),
        )
        redirect_params = {"code": code}
        if spa_state:
            redirect_params["state"] = spa_state
        separator = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(
            url=f"{redirect_uri}{separator}{urlencode(redirect_params)}"
        )
    except Exception as e:
        logger.error("app.error.unhandled", category="app", error=str(e), exc_info=True)
        await _log_login_failure(
            db, request, provider, "callback_error", error_type=type(e).__name__
        )
        return _error_page(
            500,
            "Authentication Failed",
            "Something went wrong during sign-in. Please try again.",
        )


@router.post("/workspaces", response_model=list[WorkspaceOptionResponse])
@limiter.limit(settings.rate_limit_auth)
async def list_workspaces_for_login(
    request: Request,
    body: WorkspaceListRequest,
    db: AsyncSession = Depends(get_db),
):
    """List workspaces a user belongs to (for workspace selection after OAuth).

    POST with the PKCE verifier: the auth code travels in the redirect URL
    (history/logs/Referer), so mere possession of a leaked code must not
    disclose the victim's workspace names/slugs/roles — the same proof
    /auth/token demands. The peek stays non-consuming so the subsequent
    /auth/token call can redeem the code.
    """
    code_data = await auth_code_service.peek_auth_code(body.code)
    if not code_data:
        raise HTTPException(
            status_code=400, detail="Invalid or expired authorization code"
        )

    stored_challenge = code_data.get("code_challenge")
    challenge_method = code_data.get("code_challenge_method", "S256")
    if not stored_challenge:
        raise HTTPException(
            status_code=400, detail="Authorization code missing PKCE challenge"
        )
    if not auth_code_service.verify_code_challenge(
        body.code_verifier, stored_challenge, challenge_method
    ):
        # A leaked auth code being probed without the PKCE verifier.
        log_security(
            "auth.token.rejected",
            outcome="denied",
            reason="pkce_failed",
            stage="workspace_list",
            source_ip=get_client_ip(request),
        )
        raise HTTPException(status_code=400, detail="PKCE verification failed")

    user_id = uuid.UUID(code_data["user_id"])
    user = await db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=404, detail="User not found")

    workspaces = await workspace_service.list_user_workspaces(db, user_id)

    # Filter the picker to workspaces this user can ACTUALLY mint a token for —
    # selecting one consumes the one-time auth code and then 403s at token
    # issuance, forcing a full re-login. The user's effective org and the
    # disabled-real-org kill-switch are the same checks issue_tokens applies; if
    # the org was disabled after this code was issued, nothing is mintable.
    eff_org = await organization_service.effective_org(db, user)
    if organization_service.is_disabled_real_org(eff_org):
        return []
    eff_org_id = eff_org.id if eff_org is not None else None
    allowed_ws_ids = await organization_service.filter_workspaces_allowing_org(
        db, [ws.id for ws in workspaces], eff_org_id
    )

    result = []
    for ws in workspaces:
        if ws.id not in allowed_ws_ids:
            continue
        stmt = select(WorkspaceMembership.role).where(
            WorkspaceMembership.workspace_id == ws.id,
            WorkspaceMembership.user_id == user_id,
        )
        role_result = await db.execute(stmt)
        role = role_result.scalar_one()
        result.append(
            WorkspaceOptionResponse(id=ws.id, name=ws.name, slug=ws.slug, role=role)
        )
    return result


@router.post("/token", response_model=TokenResponse)
@limiter.limit(settings.rate_limit_auth)
async def select_workspace_and_issue_tokens(
    request: Request,
    body: SelectWorkspaceRequest,
    db: AsyncSession = Depends(get_db),
):
    """Exchange authorization code + workspace_id + PKCE verifier for JWT tokens."""

    def _reject(status: int, detail: str, reason: str, **fields):
        log_security(
            "auth.token.rejected",
            outcome="denied",
            reason=reason,
            stage="token",
            source_ip=get_client_ip(request),
            **fields,
        )
        raise HTTPException(status_code=status, detail=detail)

    code_data = await auth_code_service.consume_auth_code(body.code)
    if not code_data:
        _reject(400, "Invalid or expired authorization code", "invalid_code")

    # Defense-in-depth: validate provider matches a configured IdP
    stored_provider = code_data.get("provider")
    if stored_provider and stored_provider not in get_configured_providers():
        _reject(400, "Invalid authorization code", "provider_not_configured")

    # PKCE verification — code_challenge is always present (mandatory)
    stored_challenge = code_data.get("code_challenge")
    challenge_method = code_data.get("code_challenge_method", "S256")
    if not stored_challenge:
        _reject(400, "Authorization code missing PKCE challenge", "missing_challenge")
    if not auth_code_service.verify_code_challenge(
        body.code_verifier, stored_challenge, challenge_method
    ):
        # A leaked auth code being redeemed without the PKCE verifier.
        _reject(400, "PKCE verification failed", "pkce_failed")

    user_id = uuid.UUID(code_data["user_id"])
    client_app_id = (
        uuid.UUID(code_data["client_app_id"])
        if code_data.get("client_app_id")
        else None
    )
    user = await db.get(User, user_id)
    if not user or not user.is_active:
        _reject(404, "User not found", "user_not_found", actor=str(user_id))

    workspace = await db.get(Workspace, body.workspace_id)
    if not workspace:
        _reject(404, "Workspace not found", "workspace_not_found", actor=str(user_id))

    try:
        tokens = await auth_service.issue_tokens(
            db, user, workspace.id, workspace.slug, client_app_id=client_app_id
        )
    except ValueError:
        # Non-member or org-not-permitted — a code minted for one identity
        # being pointed at a workspace it has no standing in.
        _reject(
            403,
            "Cannot issue tokens for this workspace",
            "issuance_refused",
            actor=str(user_id),
            workspace_id=str(body.workspace_id),
        )

    return tokens


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit(settings.rate_limit_auth)
async def refresh_token(
    request: Request,
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        tokens = await auth_service.rotate_refresh_token(
            db,
            body.refresh_token,
            ip=get_client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:200],
        )
    except Exception:
        logger.error(
            "app.error.unhandled",
            category="app",
            error="refresh_token_rotation_failed",
            exc_info=True,
        )
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    return tokens


@router.post("/logout")
async def logout(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Revoke all refresh token families using identity from already-validated JWT
    await token_service.revoke_all_user_tokens(str(user.user_id))

    # Best-effort: blacklist this specific access token's jti
    auth_header = request.headers.get("Authorization", "")
    token_str = auth_header.removeprefix("Bearer ")
    try:
        payload = decode_token(token_str, audience="sentinel:access")
        if jti := payload.get("jti"):
            await token_service.blacklist_access_token(jti, payload["exp"])
    except Exception:
        pass  # Token already expired — jti blacklisting not needed
    log_security(
        "auth.token.revoked",
        outcome="success",
        actor=str(user.user_id),
        reason="logout",
    )
    # Admin-visible row so logins and logouts balance in the activity feed.
    # Best-effort: an audit failure must not block sign-out.
    try:
        await activity_service.log_activity(
            db,
            action="user_logout",
            target_type="user",
            target_id=user.user_id,
            actor_id=user.user_id,
            workspace_id=user.workspace_id,
        )
        await db.commit()
    except Exception:
        log_security("audit.write_failed", outcome="failure", reason="logout_audit_row")
    response = JSONResponse({"ok": True})
    response.headers["Clear-Site-Data"] = '"cookies", "storage"'
    return response


# --- Admin auth endpoints ---


@router.get("/admin/login/{provider}")
@limiter.limit(settings.rate_limit_auth_admin)
async def admin_login(
    provider: str, request: Request, db: AsyncSession = Depends(get_db)
):
    configured = get_configured_providers()
    if provider not in configured:
        await _log_login_failure(
            db,
            request,
            provider,
            "provider_not_configured",
            flow="admin",
            count_for_stuffing=False,
            stream_event="auth.login.rejected",
        )
        raise HTTPException(
            status_code=400, detail=f"Provider '{provider}' is not configured"
        )
    client = oauth.create_client(provider)
    redirect_uri = f"{settings.base_url}/auth/admin/callback/{provider}"
    return await client.authorize_redirect(request, redirect_uri)


@router.get("/admin/callback/{provider}")
@limiter.limit(settings.rate_limit_auth_admin)
async def admin_callback(
    provider: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    try:
        configured = get_configured_providers()
        if provider not in configured:
            raise HTTPException(
                status_code=400, detail=f"Provider '{provider}' is not configured"
            )

        client = oauth.create_client(provider)
        token = await client.authorize_access_token(request)

        if provider == "github":
            resp = await client.get("user", token=token)
            profile = resp.json()
            # Always validate email via /user/emails (profile email may be unverified)
            resp = await client.get("user/emails", token=token)
            emails = resp.json()
            primary = next(
                (e for e in emails if e.get("primary") and e.get("verified")),
                None,
            )
            if not primary:
                await _log_login_failure(
                    db, request, provider, "email_not_verified", flow="admin"
                )
                return RedirectResponse(
                    url=f"{settings.admin_url}/login?error=email_not_verified",
                    status_code=302,
                )
            profile["email"] = primary["email"]
            provider_user_id = str(profile["id"])
            email = profile["email"]
            name = profile.get("name") or profile.get("login", "")
            avatar_url = profile.get("avatar_url")
        else:
            userinfo = token.get("userinfo", {})
            if not auth_service.is_email_verified_claim(userinfo, provider):
                await _log_login_failure(
                    db, request, provider, "email_not_verified", flow="admin"
                )
                return RedirectResponse(
                    url=f"{settings.admin_url}/login?error=email_not_verified",
                    status_code=302,
                )
            provider_user_id = userinfo.get("sub", "")
            email = auth_service.extract_email_claim(userinfo)
            name = userinfo.get("name", "")
            avatar_url = userinfo.get("picture")
            profile = dict(userinfo)
            if not email:
                await _log_login_failure(
                    db,
                    request,
                    provider,
                    "no_email_claim",
                    flow="admin",
                    count_for_stuffing=False,
                )
                return RedirectResponse(
                    url=f"{settings.admin_url}/login?error=no_email_claim",
                    status_code=302,
                )

        # Resolve + persist the admin's org for record-keeping, but do NOT gate
        # admin sign-in on it. Admin access is gated by is_admin (below); hard
        # org-gating here would risk locking every admin out of the panel used to
        # configure orgs (e.g. if the public org is disabled).
        org = await organization_service.resolve_organization(db, email)

        # This callback deliberately skips the org sign-in gate (so disabling the
        # public org can't lock admins out of the panel). But it must NOT become a
        # side door to JIT-provision accounts for arbitrary verified emails: decide
        # admin eligibility WITHOUT writing, and bounce ineligible sign-ins before
        # any user/social row is created.
        #
        # Key eligibility on the IDENTITY that will actually be signed in
        # (provider + provider_user_id), mirroring find_or_create_user, so the gate
        # and the provisioning never disagree. A by-email lookup is consulted ONLY
        # when this identity has no social account yet — the bare pre-provisioned
        # account it may link to. (A by-email user that already has a different
        # provider is a cross-provider conflict find_or_create_user will reject, so
        # granting eligibility on it would be deciding on an account this sign-in
        # can never become.)
        social = (
            await db.execute(
                select(SocialAccount).where(
                    SocialAccount.provider == provider,
                    SocialAccount.provider_user_id == provider_user_id,
                )
            )
        ).scalar_one_or_none()
        if social is not None:
            identity_user = await db.get(User, social.user_id)
        else:
            identity_user = (
                await db.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()
        is_admin_eligible = (
            identity_user is not None and identity_user.is_admin
        ) or email in settings.admin_email_list
        if not is_admin_eligible:
            await _log_login_failure(
                db, request, provider, "not_admin", flow="admin", email=email
            )
            return RedirectResponse(
                url=f"{settings.admin_url}/login?error=not_admin",
                status_code=302,
            )

        try:
            user = await auth_service.find_or_create_user(
                db=db,
                provider=provider,
                provider_user_id=provider_user_id,
                email=email,
                name=name,
                organization_id=org.id if org else None,
                avatar_url=avatar_url,
                provider_data=profile,
            )
        except auth_service.CrossProviderEmailConflict:
            await _log_login_failure(
                db,
                request,
                provider,
                "cross_provider_conflict",
                flow="admin",
                email=email,
            )
            return RedirectResponse(
                url=f"{settings.admin_url}/login?error=email_conflict",
                status_code=302,
            )

        if not user.is_admin:
            await _log_login_failure(
                db, request, provider, "not_admin", flow="admin", email=email
            )
            return RedirectResponse(
                url=f"{settings.admin_url}/login?error=not_admin",
                status_code=302,
            )

        await activity_service.log_activity(
            db,
            action="admin_login",
            target_type="user",
            target_id=user.id,
            actor_id=user.id,
            detail={
                "provider": provider,
                "ip": get_client_ip(request),
                "user_agent": request.headers.get("user-agent", "")[:200],
            },
        )
        await db.commit()
        await signal_service.on_login_success(
            db,
            user_id=user.id,
            ip=get_client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:200],
        )
        log_security(
            "auth.login.succeeded",
            outcome="success",
            provider=provider,
            actor=str(user.id),
            flow="admin",
        )

        admin_token = create_admin_token(
            user_id=user.id, email=user.email, name=user.name
        )
        response = RedirectResponse(url=f"{settings.admin_url}/", status_code=302)
        _cookie_opts = dict(
            httponly=True,
            secure=settings.cookie_secure,
            samesite="strict",
            max_age=3600,
        )
        response.set_cookie(
            key="admin_token", value=admin_token, path="/", **_cookie_opts
        )
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.error("app.error.unhandled", category="app", error=str(e), exc_info=True)
        await _log_login_failure(
            db,
            request,
            provider,
            "callback_error",
            flow="admin",
            error_type=type(e).__name__,
        )
        return JSONResponse(
            status_code=500, content={"detail": "Authentication failed"}
        )


@router.get("/admin/me")
async def admin_me(admin: dict = Depends(require_admin)):
    return {"id": admin["sub"], "email": admin["email"], "name": admin["name"]}


@router.post("/admin/logout")
async def admin_logout(
    request: Request,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    # Blacklist the admin token so it can't be replayed
    if jti := admin.get("jti"):
        await token_service.blacklist_access_token(jti, admin["exp"])
    log_security(
        "auth.token.revoked",
        outcome="success",
        actor=admin["sub"],
        reason="admin_logout",
    )
    # The counterpart to the admin_login row this flow always wrote.
    try:
        await activity_service.log_activity(
            db,
            action="admin_logout",
            target_type="user",
            target_id=uuid.UUID(admin["sub"]),
            actor_id=uuid.UUID(admin["sub"]),
        )
        await db.commit()
    except Exception:
        log_security(
            "audit.write_failed", outcome="failure", reason="admin_logout_audit_row"
        )
    response = JSONResponse({"ok": True})
    response.headers["Clear-Site-Data"] = '"cookies", "storage"'
    response.delete_cookie(
        "admin_token",
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
    )
    return response
