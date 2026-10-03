"""Realm self-discovery + no-user (m2m) token minting.

Both endpoints require a service key (``require_service_key``). ``whoami`` lets the
SDK self-discover its shared ``effective_scope`` with no app-side config.
``m2m-token`` mints a short-lived no-user realm token: the caller proves itself with
its service key, Duar server-stamps the token's ``caller``/``svc`` from that key
(never client-asserted), so a leaked key can only mint its own member's token — it
cannot impersonate another member or jump realms.

(Plan 3 moves this router onto the unpublished internal listener.)
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import ServiceKeyContext, require_service_key
from src.auth.jwt import create_m2m_token
from src.config import settings
from src.database import get_db
from src.logging_events import log_security
from src.middleware.rate_limit import limiter, service_or_ip_key
from src.schemas.realm import (
    M2MTokenRequest,
    M2MTokenResponse,
    RealmInfo,
    WhoamiResponse,
)
from src.services import realm_service

router = APIRouter(prefix="/realm", tags=["realm"])


@router.get("/whoami", response_model=WhoamiResponse)
@limiter.limit(settings.rate_limit_authz_resolve, key_func=service_or_ip_key)
async def whoami(
    request: Request,
    svc: ServiceKeyContext = Depends(require_service_key),
    db: AsyncSession = Depends(get_db),
):
    """Resolve the calling service key to its shared scope. The SDK caches this so
    apps carry no realm config: standalone → effective_scope is the service_name,
    realm member → effective_scope is the realm slug (+ realm name for display)."""
    realm_info: RealmInfo | None = None
    if svc.realm_slug:
        realm = await realm_service.get_realm_by_slug(db, svc.realm_slug)
        if realm is not None:
            realm_info = RealmInfo(slug=realm.slug, name=realm.name)
        else:
            # Key resolved a realm slug, but the realm row is gone (deleted, or a
            # stale key cache). Surface the anomaly; still answer with the slug scope
            # the key is operating under — 404-ing would break SDK scope discovery.
            log_security(
                "realm.whoami.orphan_realm",
                outcome="failure",
                reason="orphan_realm",
                caller_service=svc.service_name,
                realm=svc.realm_slug,
            )
    return WhoamiResponse(
        service_name=svc.service_name,
        effective_scope=svc.effective_scope,
        realm=realm_info,
    )


@router.post("/m2m-token", response_model=M2MTokenResponse)
@limiter.limit(settings.rate_limit_authz_resolve, key_func=service_or_ip_key)
async def mint_m2m_token(
    request: Request,
    body: M2MTokenRequest,
    svc: ServiceKeyContext = Depends(require_service_key),
    db: AsyncSession = Depends(get_db),
):
    """Mint a short-lived no-user realm token for an in-realm system call.

    Rejects unless the caller is an active member of an active realm — a standalone
    service has no shared scope to mint under. Identity is server-stamped from the
    authenticated key (caller=service_name, svc=realm slug), so a leaked key cannot
    impersonate another member or jump realms.
    """
    if not svc.realm_slug:
        # Leaked-standalone-key mint attempt — the docstring's threat model.
        log_security(
            "realm.m2m.denied",
            outcome="denied",
            reason="not_a_realm_member",
            caller_service=svc.service_name,
        )
        raise HTTPException(
            status_code=403,
            detail="Service is not a realm member; no-user tokens require a realm",
        )
    realm = await realm_service.get_realm_by_slug(db, svc.realm_slug)
    if realm is None or not realm.is_active:
        log_security(
            "realm.m2m.denied",
            outcome="denied",
            reason="realm_inactive",
            caller_service=svc.service_name,
            realm=svc.realm_slug,
        )
        raise HTTPException(
            status_code=403,
            detail="Realm is inactive or no longer exists",
        )

    token = create_m2m_token(
        svc=svc.effective_scope,  # realm slug — the shared audience every member checks
        caller=svc.service_name,  # server-stamped: which member minted it (audit)
        ttl_s=realm.m2m_ttl_s,
        # body.target is accepted for forward-compat but NOT honored in v1: per-call
        # aud_target narrowing is reserved future work, so we mint aud_target=None.
    )
    log_security(
        "realm.m2m.minted",
        outcome="success",
        caller_service=svc.service_name,
        realm=svc.realm_slug,
    )
    return M2MTokenResponse(token=token, expires_in=realm.m2m_ttl_s)
