import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.jwt import (
    _AUD_ACCESS,
    _AUD_REFRESH,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from src.config import settings
from src.logging_events import log_security
from src.schemas.validators import sanitize_url, strip_html
from src.models.group import GroupMembership
from src.models.user import SocialAccount, User
from src.models.workspace import WorkspaceMembership
from src.services import (
    activity_service,
    organization_service,
    signal_service,
    token_service,
)


class CrossProviderEmailConflict(Exception):
    """Raised when an IdP login's email matches a user from a different provider."""


def is_email_verified_claim(
    userinfo: dict, provider: str, *, strict: bool = False
) -> bool:
    """Check that OIDC claims assert a verified email address.

    ``strict=True`` (hosted /onboard) drops the Entra tenant-pin exemption below
    and requires an explicit ``xms_edov: True`` — the pin does not verify
    guest-account addresses.

    Per OIDC Core 1.0 §5.1, ``email_verified`` is a boolean. Some IdPs emit
    stringified booleans (``"true"``/``"false"``); the string ``"false"`` is
    truthy in Python and would bypass a naive ``not userinfo.get(...)`` check,
    letting an attacker sign in with a claimed-but-unverified email. Strictly
    compare against ``True`` so any non-boolean value fails closed.

    Microsoft Entra ID emits no ``email_verified`` claim at all — its analogue is
    the optional ``xms_edov`` ("email domain owner verified") claim. Requiring
    ``email_verified`` would therefore reject every Entra sign-in. The token has
    already been signature- and issuer-verified against the ONE tenant this
    deployment pins (``ENTRA_TENANT_ID``) by the time we get here, so the tenant
    directory is the verifying authority for addresses it issues: accept a
    same-tenant token unless ``xms_edov`` is explicitly False, which is Entra
    telling us the domain is not owner-verified.

    ``provider`` is the IdP the token was actually VERIFIED against (signature +
    issuer + audience), not a claim inside it. The relaxation is scoped to that
    provider so trust can never be decided by claim shape alone: another OIDC
    provider (a self-hosted ``dex``, a future IdP) emitting a ``tid`` equal to our
    tenant GUID must not inherit Entra's exemption.
    """
    if userinfo.get("email_verified") is True:
        return True
    if provider != "entra_id":
        return False
    if strict:
        return userinfo.get("xms_edov") is True
    # ponytail: tenant pin IS the verification. If this deployment ever accepts
    # multi-tenant Entra issuers (`organizations`/`common`), drop this branch and
    # require `xms_edov is True` — a foreign tenant vouches only for itself.
    tenant = settings.entra_tenant_id
    return (
        bool(tenant)
        and userinfo.get("tid") == tenant
        and userinfo.get("xms_edov") is not False
    )


def extract_email_claim(userinfo: dict) -> str:
    """Return the email address from OIDC claims, or ``""`` when there is none.

    Entra omits ``email`` for managed work accounts unless the tenant adds it as
    an optional claim on the app registration — very common on ``*.onmicrosoft``
    dev tenants, where accounts have no mail attribute at all. ``preferred_username``
    carries the UPN there, which is the address the user actually signs in with,
    so fall back to it when it is address-shaped.

    Identity is keyed on ``sub`` (see :func:`find_or_create_user`), never on this
    value — ``preferred_username`` is mutable and must not be treated as an ID.
    """
    email = userinfo.get("email") or ""
    if email:
        return email
    upn = userinfo.get("preferred_username") or userinfo.get("upn") or ""
    return upn if upn.count("@") == 1 else ""


async def find_or_create_user(
    db: AsyncSession,
    provider: str,
    provider_user_id: str,
    email: str,
    name: str,
    organization_id: uuid.UUID | None,
    avatar_url: str | None = None,
    provider_data: dict | None = None,
) -> User:
    """Find existing user by social account or create a new one.

    ``organization_id`` is the caller-resolved org for this email (None when the
    caller does not org-gate, e.g. admin sign-in). Sign-in policy (rejecting an
    unresolved org) lives in the route, not here.
    """
    avatar_url = sanitize_url(avatar_url)
    # Check if social account exists
    stmt = select(SocialAccount).where(
        SocialAccount.provider == provider,
        SocialAccount.provider_user_id == provider_user_id,
    )
    result = await db.execute(stmt)
    social_account = result.scalar_one_or_none()

    if social_account:
        user = await db.get(User, social_account.user_id)
        # Update profile from provider (sanitize IdP data)
        user.name = strip_html(name)
        # Refresh the org when the caller resolved one; never clobber a known org
        # with None (e.g. an admin sign-in whose domain matched no enabled org).
        if organization_id is not None:
            user.organization_id = organization_id
        if avatar_url:
            user.avatar_url = avatar_url
        social_account.provider_data = provider_data
        await db.commit()
        return user

    # Security: never auto-link across providers by email. Two different IdPs
    # reporting the same email are not necessarily the same human — especially
    # when one IdP has weaker verification than the other. Identity is keyed on
    # (provider, provider_user_id) only.
    stmt = select(User).where(User.email == email)
    existing = (await db.execute(stmt)).scalar_one_or_none()

    if existing is not None:
        # Does this user already have ANY social account? If so, they signed up
        # via a different provider — a genuine cross-provider collision; reject
        # so a support flow (or future explicit link UI) handles it intentionally.
        sa_stmt = select(SocialAccount.id).where(SocialAccount.user_id == existing.id)
        has_social = (await db.execute(sa_stmt)).scalar_one_or_none() is not None
        if has_social:
            raise CrossProviderEmailConflict(
                f"An account with email {email!r} exists under a different identity "
                "provider. Sign in with the original provider, or contact an "
                "administrator to link the accounts."
            )

        # No social account = an admin-pre-provisioned (e.g. CSV-imported) bare
        # account. Link this provider to it so the user can sign in, instead of
        # locking them out of an account created for exactly this purpose.
        existing.name = strip_html(name)
        # Refresh the org when resolved; never clobber a known org with None.
        if organization_id is not None:
            existing.organization_id = organization_id
        if avatar_url:
            existing.avatar_url = avatar_url
        db.add(
            SocialAccount(
                user_id=existing.id,
                provider=provider,
                provider_user_id=provider_user_id,
                provider_data=provider_data,
            )
        )
        if existing.email in settings.admin_email_list and not existing.is_admin:
            existing.is_admin = True
        await db.commit()
        return existing

    user = User(
        email=email,
        name=strip_html(name),
        avatar_url=avatar_url,
        organization_id=organization_id,
    )
    db.add(user)
    await db.flush()

    social_account = SocialAccount(
        user_id=user.id,
        provider=provider,
        provider_user_id=provider_user_id,
        provider_data=provider_data,
    )
    db.add(social_account)

    # Auto-promote admin emails from config
    if user.email in settings.admin_email_list and not user.is_admin:
        user.is_admin = True

    await db.commit()
    return user


async def issue_tokens(
    db: AsyncSession,
    user: User,
    workspace_id: uuid.UUID,
    workspace_slug: str,
    client_app_id: uuid.UUID | None = None,
) -> dict[str, str]:
    """Issue access + refresh tokens for a user in a workspace context."""
    # Get workspace role
    stmt = select(WorkspaceMembership).where(
        WorkspaceMembership.workspace_id == workspace_id,
        WorkspaceMembership.user_id == user.id,
    )
    result = await db.execute(stmt)
    membership = result.scalar_one_or_none()
    if not membership:
        raise ValueError("User is not a member of this workspace")

    if not await organization_service.workspace_allows_org(
        db, workspace_id, user.organization_id
    ):
        raise ValueError("User's organization is not permitted in this workspace")

    # Get group IDs
    stmt = (
        select(GroupMembership.group_id)
        .join(GroupMembership.group)
        .where(
            GroupMembership.user_id == user.id,
            GroupMembership.group.has(workspace_id=workspace_id),
        )
    )
    result = await db.execute(stmt)
    group_ids = [row[0] for row in result.all()]

    org = await organization_service.org_for_claims(db, user.organization_id)
    access_token = create_access_token(
        user_id=user.id,
        email=user.email,
        name=user.name,
        workspace_id=workspace_id,
        workspace_slug=workspace_slug,
        workspace_role=membership.role,
        groups=group_ids,
        **organization_service.org_claims(org),
    )
    # family_id is generated inside create_refresh_token and embedded in the JWT
    refresh_token = create_refresh_token(user_id=user.id)

    # Store refresh token in Redis for rotation tracking. access_jti is bound
    # to the refresh record so revoke_token_family can blacklist the paired
    # access token on reuse detection — without this, the access token remains
    # valid for the remainder of its TTL after a theft signal fires.
    rt_payload = decode_token(refresh_token, audience=_AUD_REFRESH)
    at_payload = decode_token(access_token, audience=_AUD_ACCESS)
    family_id = rt_payload["fid"]
    await token_service.store_refresh_token(
        jti=rt_payload["jti"],
        user_id=user.id,
        family_id=family_id,
        workspace_id=workspace_id,
        client_app_id=client_app_id,
        access_jti=at_payload["jti"],
    )

    # The session-creation record: every access+refresh pair minted, with the
    # family_id that later reuse/revocation events correlate on.
    log_security(
        "auth.token.issued",
        outcome="success",
        actor=str(user.id),
        workspace_id=str(workspace_id),
        client_app_id=str(client_app_id) if client_app_id else None,
        family_id=family_id,
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": settings.access_token_expire_minutes * 60,
    }


async def rotate_refresh_token(
    db: AsyncSession,
    refresh_token_str: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict[str, str]:
    """Consume a refresh token and issue a new token pair.

    Implements refresh token rotation with reuse detection:
    - If the token is valid, consume it, issue new pair, same family.
    - If the token was already consumed, revoke the entire family (theft signal).
    """
    try:
        payload = decode_token(refresh_token_str, audience=_AUD_REFRESH)
    except Exception:
        # Malformed/expired/bad-signature refresh tokens are the brute-force
        # signal; without this they only surface as category=app noise.
        log_security(
            "auth.token.refresh_rejected",
            outcome="denied",
            reason="invalid_token",
        )
        raise ValueError("Invalid refresh token")

    jti = payload["jti"]
    result = await token_service.consume_refresh_token(jti)

    if result is None:
        # Already consumed or expired — possible theft.
        # Extract family_id from the JWT and revoke the entire family.
        # (Benign concurrent double-submits are prevented client-side by the
        # SDK's single-flight refresh; the server stays strict on reuse.)
        family_id = payload.get("fid")
        actor = payload.get("sub")
        log_security(
            "auth.token.reuse_detected",
            outcome="failure",
            reason="refresh_reuse",
            actor=str(actor) if actor else None,
            family_id=family_id,
        )
        if family_id:
            await token_service.revoke_token_family(family_id)
        # Best-effort admin-visible audit row — the theft signal must land in
        # the activity log, but logging failure must not mask the 401.
        try:
            actor_uuid = uuid.UUID(str(actor)) if actor else None
            await activity_service.log_activity(
                db,
                action="refresh_reuse_detected",
                target_type="user" if actor_uuid else "system",
                target_id=actor_uuid or uuid.UUID(int=0),
                actor_id=actor_uuid,
                detail={
                    "family_id": family_id,
                    "ip": ip,
                    "user_agent": user_agent,
                },
            )
            await db.commit()
        except Exception:
            log_security(
                "audit.write_failed",
                outcome="failure",
                reason="refresh_reuse_audit_row",
            )
        raise ValueError("Refresh token already used or expired")

    user_id, family_id, workspace_id, client_app_id = result

    # The old refresh token is now consumed (one-time use). Any failure from here
    # on must revoke the whole family — otherwise a partially-rotated session
    # (old token spent, no new pair issued, siblings still live) could linger.
    # This covers every validation failure (inactive user, lost membership, org
    # not permitted, disabled-tenant kill-switch) AND any unexpected error, so the
    # path always fails closed.
    try:
        user = await db.get(User, user_id)
        if not user or not user.is_active:
            raise ValueError("User not found or inactive")

        # Verify user still belongs to the original workspace
        stmt = select(WorkspaceMembership).where(
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.workspace_id == workspace_id,
        )
        db_result = await db.execute(stmt)
        membership = db_result.scalar_one_or_none()
        if not membership:
            raise ValueError("User is no longer a member of this workspace")

        if not await organization_service.workspace_allows_org(
            db, workspace_id, user.organization_id
        ):
            raise ValueError("User's organization is not permitted in this workspace")

        # Get workspace slug
        from src.models.workspace import Workspace

        workspace = await db.get(Workspace, workspace_id)

        # Get group IDs
        stmt = (
            select(GroupMembership.group_id)
            .join(GroupMembership.group)
            .where(
                GroupMembership.user_id == user.id,
                GroupMembership.group.has(workspace_id=workspace_id),
            )
        )
        db_result = await db.execute(stmt)
        group_ids = [row[0] for row in db_result.all()]

        # Enforce the real-org kill-switch on refresh too: a disabled tenant must
        # not keep its sessions alive by rotating. org_for_claims raises OrgDisabled,
        # which the wrapper turns into a family revocation like any other failure.
        org = await organization_service.org_for_claims(db, user.organization_id)

        # Issue new tokens
        new_access = create_access_token(
            user_id=user.id,
            email=user.email,
            name=user.name,
            workspace_id=workspace_id,
            workspace_slug=workspace.slug,
            workspace_role=membership.role,
            groups=group_ids,
            **organization_service.org_claims(org),
        )
        new_refresh = create_refresh_token(user_id=user.id, family_id=family_id)

        # Store new refresh token in same family, binding paired access jti so
        # family revocation can blacklist it.
        new_rt_payload = decode_token(new_refresh, audience=_AUD_REFRESH)
        new_at_payload = decode_token(new_access, audience=_AUD_ACCESS)
        await token_service.store_refresh_token(
            jti=new_rt_payload["jti"],
            user_id=user.id,
            family_id=family_id,
            workspace_id=workspace_id,
            client_app_id=client_app_id,
            access_jti=new_at_payload["jti"],
        )
    except Exception as e:
        await token_service.revoke_token_family(family_id)
        # A whole session family just died fail-closed (deactivated user, lost
        # membership, org kill-switch, or unexpected error) — record it in both
        # channels; the reuse path above already does the same for theft.
        log_security(
            "auth.token.family_revoked",
            outcome="denied",
            reason=str(e)[:200] or type(e).__name__,
            actor=str(user_id),
            family_id=family_id,
        )
        try:
            await db.rollback()
            await activity_service.log_activity(
                db,
                action="token_family_revoked",
                target_type="user",
                target_id=user_id,
                actor_id=user_id,
                workspace_id=workspace_id,
                detail={"family_id": family_id, "reason": str(e)[:200]},
            )
            await db.commit()
        except Exception:
            log_security(
                "audit.write_failed",
                outcome="failure",
                reason="family_revoked_audit_row",
            )
        raise

    # Session-context anomaly signal: same token family suddenly refreshing
    # from a different ip/user-agent (possible token theft). Deliberately
    # OUTSIDE the fail-closed block above — telemetry failure must never
    # revoke a healthy family or break the refresh.
    if ip or user_agent:
        try:
            prev = await token_service.swap_refresh_context(
                family_id, ip or "", (user_agent or "")[:200]
            )
            if prev is not None:
                await activity_service.log_activity(
                    db,
                    action="refresh_context_changed",
                    target_type="user",
                    target_id=user.id,
                    actor_id=user.id,
                    workspace_id=workspace_id,
                    detail={
                        "family_id": family_id,
                        "ip": ip,
                        "user_agent": (user_agent or "")[:200],
                        "prev_ip": prev.get("ip"),
                        "prev_user_agent": prev.get("ua"),
                    },
                )
                await db.commit()
                await signal_service.on_refresh_ip_changed(
                    db,
                    user_id=user.id,
                    workspace_id=workspace_id,
                    ip=ip or "",
                    user_agent=(user_agent or "")[:200],
                )
        except Exception:
            log_security(
                "audit.write_failed",
                outcome="failure",
                reason="refresh_context_audit_row",
            )

    log_security(
        "auth.token.refreshed",
        outcome="success",
        actor=str(user.id),
    )
    return {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "token_type": "bearer",
        "expires_in": settings.access_token_expire_minutes * 60,
    }
