"""Duar-hosted self-serve onboarding pages (/onboard).

Server-rendered, zero-JavaScript pages where a user with no workspace joins one
through a one-time invitation link or creates one, and where workspace
owners/admins mint invitations. Identity comes from Duar's own OAuth client
flow (authlib code flow, like proxy mode and admin login) and lives in the
existing signed session cookie under ``onboard_*`` keys. Everything is gated
by ``SELF_SERVE_ENABLED`` (404 when off). See docs/guide/self-serve.md and
docs/superpowers/specs/2026-08-25-self-serve-workspaces-design.md.
"""

import hmac
import secrets
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import structlog
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import HTMLResponse, RedirectResponse, Response

from src.api.auth_routes import (
    IdpProfileError,
    _error_page,
    _idp_profile,
    _log_login_failure,
    _profile_error_page,
)
from src.api.authz_routes import service_app_origin_allowed
from src.auth.providers import get_configured_providers, oauth
from src.config import settings
from src.database import get_db
from src.logging_events import log_security
from src.middleware.rate_limit import get_client_ip, limiter
from src.models.user import User
from src.models.workspace import Workspace
from src.schemas.validators import strip_html
from src.services import (
    activity_service,
    auth_service,
    invitation_service,
    organization_service,
    signal_service,
    workspace_service,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/onboard", tags=["onboard"], include_in_schema=False)

_TEMPLATES_DIR = str(Path(__file__).resolve().parent.parent / "templates")
templates = Jinja2Templates(
    env=Environment(loader=FileSystemLoader(_TEMPLATES_DIR), autoescape=True)
)

# Keys GET /onboard resets. NEVER onboard_user_id / onboard_csrf, and never
# request.session.clear(): the same cookie carries in-flight proxy/admin/authz-idp
# OAuth round-trips from other tabs.
_RESET_KEYS = (
    "onboard_code",
    "onboard_return_to",
    "onboard_next",
    "onboard_flash",
    "onboard_result",
)
_MAX_RETURN_TO = 2048


def _require_enabled() -> None:
    if not settings.self_serve_enabled:
        raise HTTPException(status_code=404, detail="Not found")


def _url(path: str = "") -> str:
    return f"{settings.base_url}/onboard{path}"


def _home() -> str:
    return _url("/home")


def _render(request: Request, template: str, status: int = 200, **ctx) -> HTMLResponse:
    flash = request.session.pop("onboard_flash", None)
    resp = templates.TemplateResponse(
        request,
        f"onboard/{template}",
        {
            "base_url": settings.base_url,
            "flash": flash["text"] if isinstance(flash, dict) else flash,
            "flash_ok": bool(flash.get("ok")) if isinstance(flash, dict) else False,
            "csrf": request.session.get("onboard_csrf", ""),
            "return_to": request.session.get("onboard_return_to"),
            **ctx,
        },
        status_code=status,
    )
    resp.headers["X-CSP-Override"] = "html-page"
    return resp


def _flash(request: Request, text: str, ok: bool = False) -> None:
    request.session["onboard_flash"] = {"text": text, "ok": ok}


def _clear_onboard_session(request: Request) -> None:
    for key in [k for k in request.session if k.startswith("onboard_")]:
        del request.session[key]


async def _session_user(request: Request, db: AsyncSession) -> User | None:
    """The onboarding user, re-read every request (deactivation cuts the
    session off). Writes ``onboard_seen`` so the 10-minute cookie window slides
    (starlette re-issues the cookie only when the session is modified)."""
    raw = request.session.get("onboard_user_id")
    if not raw:
        return None
    try:
        user = await db.get(User, uuid.UUID(raw))
    except (TypeError, ValueError):
        user = None
    if user is None or not user.is_active:
        _clear_onboard_session(request)
        return None
    request.session["onboard_seen"] = int(time.time())
    return user


def _csrf_ok(request: Request, token: str | None) -> bool:
    expected = request.session.get("onboard_csrf")
    return bool(expected and token) and hmac.compare_digest(expected, token)


def _csrf_page() -> HTMLResponse:
    return _error_page(
        403,
        "Invalid Form Token",
        "Please reload the page and try again.",
        back_href=_home(),
    )


async def _store_return_to(
    request: Request, db: AsyncSession, value: str
) -> HTMLResponse | None:
    """Validate ``return_to`` against the ServiceApp origin allowlist and stash
    it; returns an error page instead of storing anything on failure.

    Uses the pure ``service_app_origin_allowed`` predicate (not
    ``_validate_authz_redirect_uri``): /onboard is public and delivers nothing
    to ``return_to``, so a rejection here is an ordinary denied redirect, not
    the token-exfil-severity event the idp-proxy flow logs.
    """
    if len(value) > _MAX_RETURN_TO:
        return _error_page(
            400,
            "Invalid Return URL",
            "The return address is too long.",
            back_href=_url(),
        )
    if not await service_app_origin_allowed(db, value):
        parsed = urlparse(value)
        origin = (
            f"{parsed.scheme}://{parsed.netloc}"
            if parsed.scheme and parsed.hostname
            else None
        )
        log_security(
            "onboard.return_to_rejected",
            outcome="denied",
            reason="not_allowed",
            origin=origin,
        )
        return _error_page(
            400,
            "Invalid Return URL",
            "The app you came from is not registered on this server.",
            back_href=_url(),
        )
    request.session["onboard_return_to"] = value
    return None


def _client_meta(request: Request) -> dict:
    return {
        "ip": get_client_ip(request),
        "user_agent": request.headers.get("user-agent", "")[:200],
    }


# ── entry / login / callback ──────────────────────────────────────────


def _next_or_home(request: Request) -> str:
    """Pop ``onboard_next`` and use it only if it stays inside /onboard —
    otherwise a session carrying an attacker-supplied absolute URL there
    (e.g. planted before a same-cookie-domain redirect) becomes an open
    redirect on sign-in."""
    nxt = request.session.pop("onboard_next", None)
    return nxt if nxt and nxt.startswith(_url()) else _home()


@router.get("", response_class=HTMLResponse)
async def entry(
    request: Request,
    return_to: str | None = None,
    code: str | None = None,
    provider: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    _require_enabled()
    if return_to or code:
        # Validate BEFORE touching the session: a rejected return_to must
        # leave any earlier round (code/return_to/next) untouched. Empty
        # string ("return_to=") is treated as absent, not as a value to
        # validate — an empty return_to must never drop an accompanying code.
        if return_to:
            err = await _store_return_to(request, db, return_to)
            if err is not None:
                return err
        # Now that this round can't fail: start a fresh round (clears any
        # stale code/return_to/next/flash/result from an earlier one), then
        # store what's fresh. _store_return_to already wrote onboard_return_to
        # above; the reset loop below pops it right back out, so it's set
        # again here — simplest way to keep both "validate first" and
        # "reset-then-set" true at once.
        for key in _RESET_KEYS:
            request.session.pop(key, None)
        if return_to:
            request.session["onboard_return_to"] = return_to
        if code:
            request.session["onboard_code"] = code.strip()[:128]
        # PRG: the code never persists in history / the address bar.
        return RedirectResponse(_url(), status_code=303)

    if await _session_user(request, db) is not None:
        return RedirectResponse(_next_or_home(request), status_code=302)

    providers = get_configured_providers()
    target = (
        provider
        if provider in providers
        else (providers[0] if len(providers) == 1 else None)
    )
    if target:
        return RedirectResponse(_url(f"/login/{target}"), status_code=302)
    return _render(request, "login.html", providers=providers)


@router.get("/login/{provider}")
@limiter.limit(settings.rate_limit_auth)
async def login(provider: str, request: Request):
    _require_enabled()
    if provider not in get_configured_providers():
        return _error_page(
            400,
            "Provider Not Available",
            f"The login provider “{provider}” is not configured on this server.",
            back_href=_url(),
        )
    client = oauth.create_client(provider)
    return await client.authorize_redirect(request, _url(f"/callback/{provider}"))


@router.get("/callback/{provider}")
@limiter.limit(settings.rate_limit_auth)
async def callback(provider: str, request: Request, db: AsyncSession = Depends(get_db)):
    _require_enabled()
    back = _url()
    try:
        if provider not in get_configured_providers():
            return _error_page(
                400,
                "Provider Not Available",
                "This provider is not configured.",
                back_href=back,
            )
        client = oauth.create_client(provider)
        token = await client.authorize_access_token(request)
        try:
            prof = await _idp_profile(client, token, provider)
        except IdpProfileError as e:
            await _log_login_failure(
                db,
                request,
                provider,
                e.reason,
                flow="onboard",
                count_for_stuffing=e.count_for_stuffing,
            )
            return _profile_error_page(provider, e.reason, back_href=back)

        if provider == "entra_id" and prof.provider_data.get("xms_edov") is not True:
            # The tenant pin does not verify guest-account addresses; the
            # optional xms_edov claim does. Fail closed on the hosted flow.
            await _log_login_failure(
                db,
                request,
                provider,
                "email_domain_unverified",
                flow="onboard",
                count_for_stuffing=False,
            )
            return _error_page(
                403,
                "Email Not Verified",
                "Your Microsoft account's email domain is not owner-verified (xms_edov).",
                back_href=back,
            )

        org = await organization_service.resolve_organization(db, prof.email)
        if org is None:
            await _log_login_failure(
                db,
                request,
                provider,
                "org_not_permitted",
                flow="onboard",
                email=prof.email,
            )
            return _error_page(
                403,
                "Sign-In Not Permitted",
                "Your email domain is not associated with an organization on this "
                "server, and public sign-in is disabled. Contact your administrator.",
                back_href=back,
            )
        try:
            user = await auth_service.find_or_create_user(
                db=db,
                provider=provider,
                provider_user_id=prof.provider_user_id,
                email=prof.email,
                name=prof.name,
                organization_id=org.id,
                avatar_url=prof.avatar_url,
                provider_data=prof.provider_data,
            )
        except auth_service.CrossProviderEmailConflict:
            await _log_login_failure(
                db,
                request,
                provider,
                "cross_provider_conflict",
                flow="onboard",
                email=prof.email,
            )
            return _error_page(
                409,
                "Email Already Used",
                "An account with this email address already exists under a "
                "different sign-in provider. Please sign in with your original "
                "provider, or contact your administrator to link accounts.",
                back_href=back,
            )
        if not user.is_active:
            await _log_login_failure(
                db,
                request,
                provider,
                "inactive_user",
                flow="onboard",
                count_for_stuffing=False,
            )
            return _error_page(
                403,
                "Account Inactive",
                "This account has been deactivated.",
                back_href=back,
            )

        meta = _client_meta(request)
        await activity_service.log_activity(
            db,
            action="user_login",
            target_type="user",
            target_id=user.id,
            actor_id=user.id,
            detail={"provider": provider, "flow": "onboard", **meta},
        )
        await db.commit()
        await signal_service.on_login_success(
            db, user_id=user.id, ip=meta["ip"], user_agent=meta["user_agent"]
        )
        log_security(
            "auth.login.succeeded",
            outcome="success",
            provider=provider,
            actor=str(user.id),
            flow="onboard",
        )

        request.session["onboard_user_id"] = str(user.id)
        request.session["onboard_csrf"] = secrets.token_urlsafe(32)
        request.session["onboard_seen"] = int(time.time())
        return RedirectResponse(_next_or_home(request), status_code=302)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("app.error.unhandled", category="app", error=str(e), exc_info=True)
        await _log_login_failure(
            db,
            request,
            provider,
            "callback_error",
            flow="onboard",
            error_type=type(e).__name__,
        )
        return _error_page(
            500,
            "Authentication Failed",
            "Something went wrong during sign-in. Please try again.",
            back_href=back,
        )


# ── home / done / logout ──────────────────────────────────────────────


@router.get("/home", response_class=HTMLResponse)
async def home(request: Request, db: AsyncSession = Depends(get_db)):
    _require_enabled()
    user = await _session_user(request, db)
    if user is None:
        return RedirectResponse(_url(), status_code=302)
    now = datetime.now(UTC)
    ctx: dict = {
        "invite": None,
        "invite_ws": None,
        "invite_member": False,
        "invite_invalid": False,
        "invite_code": None,
    }
    code = request.session.get("onboard_code")
    if code:
        inv = await invitation_service.peek(db, code, now=now)
        if inv is None:
            ctx["invite_invalid"] = True
            request.session.pop("onboard_code", None)
        else:
            ctx.update(
                invite=inv,
                invite_ws=await db.get(Workspace, inv.workspace_id),
                invite_member=(
                    await workspace_service.get_member_role(
                        db, inv.workspace_id, user.id
                    )
                )
                is not None,
                invite_code=code,
            )
    cap = settings.self_serve_max_workspaces_per_user
    created = await workspace_service.count_created_by(db, user.id)
    return _render(
        request,
        "home.html",
        user=user,
        workspaces=await workspace_service.list_user_workspaces(db, user.id),
        show_create_section=cap > 0,
        can_create=created < cap,
        **ctx,
    )


@router.get("/done", response_class=HTMLResponse)
async def done(request: Request, db: AsyncSession = Depends(get_db)):
    _require_enabled()
    user = await _session_user(request, db)
    if user is None:
        return RedirectResponse(_url(), status_code=302)
    result = request.session.pop("onboard_result", None)
    request.session.pop("onboard_code", None)
    return _render(request, "done.html", result=result)


@router.post("/logout")
async def logout(
    request: Request, csrf: str = Form(""), db: AsyncSession = Depends(get_db)
):
    _require_enabled()
    if await _session_user(request, db) is None:
        return RedirectResponse(_url(), status_code=303)
    if not _csrf_ok(request, csrf):
        return _csrf_page()
    _clear_onboard_session(request)
    return RedirectResponse(_url(), status_code=303)


# ── join / create ─────────────────────────────────────────────────────


def _extract_code(value: str) -> str:
    """Accept a bare code or a full ``/onboard?code=…`` link."""
    value = value.strip()
    if "code=" in value:
        return (parse_qs(urlparse(value).query).get("code") or [""])[0].strip()
    return value


@router.post("/join")
@limiter.limit(settings.rate_limit_auth)
async def join(
    request: Request,
    code: str = Form(...),
    csrf: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    _require_enabled()
    user = await _session_user(request, db)
    if user is None:
        return RedirectResponse(_url(), status_code=303)
    if not _csrf_ok(request, csrf):
        return _csrf_page()
    code = _extract_code(code)[:256]
    try:
        inv = await invitation_service.redeem(db, user, code)
    except invitation_service.InvitationInvalid:
        log_security(
            "invitation.rejected",
            outcome="denied",
            reason="invalid",
            actor=str(user.id),
        )
        await activity_service.log_activity(
            db,
            action="invitation_rejected",
            target_type="user",
            target_id=user.id,
            actor_id=user.id,
            detail={"reason": "invalid"},
        )
        await db.commit()
        _flash(request, "This invitation is invalid, expired, or already used.")
        return RedirectResponse(_home(), status_code=303)
    except ValueError as e:  # org allowlist — about the user, safe to show
        log_security(
            "invitation.rejected",
            outcome="denied",
            reason="org_not_allowed",
            actor=str(user.id),
        )
        await activity_service.log_activity(
            db,
            action="invitation_rejected",
            target_type="user",
            target_id=user.id,
            actor_id=user.id,
            detail={"reason": "org_not_allowed"},
        )
        await db.commit()
        _flash(request, str(e))
        return RedirectResponse(_home(), status_code=303)
    ws = await db.get(Workspace, inv.workspace_id)
    await activity_service.log_activity(
        db,
        action="invitation_accepted",
        target_type="workspace",
        target_id=ws.id,
        actor_id=user.id,
        workspace_id=ws.id,
        detail={"role": inv.role},
    )
    await db.commit()
    log_security(
        "invitation.accepted",
        outcome="success",
        actor=str(user.id),
        workspace_id=str(ws.id),
        role=inv.role,
    )
    request.session.pop("onboard_code", None)
    request.session["onboard_result"] = {"kind": "joined", "workspace": ws.name}
    return RedirectResponse(_url("/done"), status_code=303)


@router.post("/create")
@limiter.limit(settings.rate_limit_auth)
async def create(
    request: Request,
    name: str = Form(...),
    csrf: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    _require_enabled()
    user = await _session_user(request, db)
    if user is None:
        return RedirectResponse(_url(), status_code=303)
    if not _csrf_ok(request, csrf):
        return _csrf_page()
    name = strip_html(name)[:255]  # SafeStr does not apply to Form() params
    if not name:
        _flash(request, "A workspace name is required.")
        return RedirectResponse(_home(), status_code=303)
    try:
        ws = await workspace_service.create_self_serve(db, user, name)
    except workspace_service.SelfServeCapReached:
        return await _deny_create(
            request,
            db,
            user,
            "cap",
            "You've reached the limit of workspaces you can create.",
        )
    except workspace_service.SelfServeThrottled:
        await _deny_create(request, db, user, "throttled", None)
        return _error_page(
            429,
            "Too Many Workspaces",
            "Too many workspaces are being created right now — try again later.",
            back_href=_home(),
        )
    await activity_service.log_activity(
        db,
        action="workspace_created",
        target_type="workspace",
        target_id=ws.id,
        actor_id=user.id,
        workspace_id=ws.id,
        detail={"name": ws.name, "slug": ws.slug, "self_serve": True},
    )
    await db.commit()
    log_security(
        "workspace.self_serve.created",
        outcome="success",
        actor=str(user.id),
        workspace_id=str(ws.id),
    )
    request.session["onboard_result"] = {"kind": "created", "workspace": ws.name}
    return RedirectResponse(_url("/done"), status_code=303)


async def _deny_create(
    request: Request, db: AsyncSession, user: User, reason: str, flash: str | None
) -> Response:
    log_security(
        "workspace.self_serve.denied",
        outcome="denied",
        reason=reason,
        actor=str(user.id),
    )
    await activity_service.log_activity(
        db,
        action="self_serve_denied",
        target_type="user",
        target_id=user.id,
        actor_id=user.id,
        detail={"reason": reason},
    )
    await db.commit()
    if flash:
        _flash(request, flash)
    return RedirectResponse(_home(), status_code=303)


# ── inviter side ──────────────────────────────────────────────────────


def _invites_url(workspace_id: uuid.UUID | None) -> str:
    return _url("/invites") + (f"?workspace={workspace_id}" if workspace_id else "")


@router.get("/invites", response_class=HTMLResponse)
async def invites(
    request: Request,
    workspace: str | None = None,
    return_to: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    _require_enabled()
    try:
        wanted = uuid.UUID(workspace) if workspace else None
    except ValueError:
        wanted = None
    if return_to is not None:
        err = await _store_return_to(request, db, return_to)
        if err is not None:
            return err
    user = await _session_user(request, db)
    if user is None:
        # Only ever a value WE build from a validated uuid — never raw input.
        request.session["onboard_next"] = _invites_url(wanted)
        return RedirectResponse(_url(), status_code=302)
    admin_ws = await workspace_service.list_admin_workspaces(db, user.id)
    selected = next(
        (ws for ws, _r in admin_ws if ws.id == wanted),
        admin_ws[0][0] if admin_ws else None,
    )
    pending = (
        await invitation_service.list_for_workspace(db, selected.id) if selected else []
    )
    return _render(
        request,
        "invites.html",
        admin_workspaces=admin_ws,
        selected=selected,
        pending=pending,
        new_invite=request.session.pop("onboard_new_invite", None),
    )


@router.post("/invites")
@limiter.limit(settings.rate_limit_auth)
async def create_invite(
    request: Request,
    workspace_id: uuid.UUID = Form(...),
    role: str = Form("viewer"),
    email: str = Form(""),
    csrf: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    _require_enabled()
    user = await _session_user(request, db)
    if user is None:
        return RedirectResponse(_url(), status_code=303)
    if not _csrf_ok(request, csrf):
        return _csrf_page()
    actor_role = await workspace_service.get_member_role(db, workspace_id, user.id)
    if actor_role not in ("owner", "admin"):
        return _error_page(
            403,
            "Not Allowed",
            "Only workspace owners and admins can invite.",
            back_href=_invites_url(None),
        )
    try:
        inv, code = await invitation_service.create(
            db,
            workspace_id=workspace_id,
            role=role,
            created_by=user.id,
            actor_role=actor_role,
            email=email.strip() or None,
        )
    except ValueError as e:
        _flash(request, str(e))
        return RedirectResponse(_invites_url(workspace_id), status_code=303)
    await activity_service.log_activity(
        db,
        action="invitation_created",
        target_type="workspace",
        target_id=workspace_id,
        actor_id=user.id,
        workspace_id=workspace_id,
        detail={"role": role, "locked": bool(inv.email)},
    )
    await db.commit()
    log_security(
        "invitation.created",
        outcome="success",
        actor=str(user.id),
        workspace_id=str(workspace_id),
        role=role,
        locked=bool(inv.email),
    )
    params = {"code": code}
    if request.session.get("onboard_return_to"):
        params["return_to"] = request.session["onboard_return_to"]
    # One-time display: PRG so a refresh does not re-POST; the next GET pops it.
    request.session["onboard_new_invite"] = {
        "link": f"{_url()}?{urlencode(params)}",
        "code": code,
    }
    return RedirectResponse(_invites_url(workspace_id), status_code=303)


@router.post("/invites/{invitation_id}/revoke")
async def revoke_invite(
    request: Request,
    invitation_id: uuid.UUID,
    csrf: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    _require_enabled()
    user = await _session_user(request, db)
    if user is None:
        return RedirectResponse(_url(), status_code=303)
    if not _csrf_ok(request, csrf):
        return _csrf_page()
    try:
        inv = await invitation_service.revoke(db, invitation_id, actor_id=user.id)
    except (invitation_service.InvitationInvalid, PermissionError):
        _flash(request, "That invitation could not be revoked.")
        return RedirectResponse(_invites_url(None), status_code=303)
    await activity_service.log_activity(
        db,
        action="invitation_revoked",
        target_type="workspace",
        target_id=inv.workspace_id,
        actor_id=user.id,
        workspace_id=inv.workspace_id,
    )
    await db.commit()
    log_security(
        "invitation.revoked",
        outcome="success",
        actor=str(user.id),
        workspace_id=str(inv.workspace_id),
    )
    _flash(request, "Invitation revoked.", ok=True)
    return RedirectResponse(_invites_url(inv.workspace_id), status_code=303)
