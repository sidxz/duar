"""Dual-token middleware for AuthZ mode.

Validates both an IdP token (identity) and a Duar authz token
(authorization), checking that the idp_sub claims match.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import OrderedDict
from typing import TYPE_CHECKING

import httpx
import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from duar_auth.types import AuthenticatedUser, DuarError

if TYPE_CHECKING:
    from duar_auth.duar import Duar

_RESOLVE_CACHE_MAX = 4096
_MISSING_BOTH_DETAIL = "Missing authz token — send X-Authz-Token, or X-Workspace-Id to resolve one server-side"


class AuthzMiddleware(BaseHTTPMiddleware):
    """Validates IdP token + Duar authz token on each request.

    IdP token: ``Authorization: Bearer <idp_token>``
    Authz token: ``X-Authz-Token: <authz_token>``

    Both must be valid and their ``sub``/``idp_sub`` claims must match.

    Required binding arguments:
    - ``service_name``: the authz token's ``svc`` claim must equal this, so a
      token minted for another service cannot be replayed here.
    - ``idp_audience``: the IdP token's ``aud`` claim must equal this. In
      OpenID Connect this is your OAuth client_id. Without this check, any
      valid ID token from any client of the same IdP authenticates.
    - ``idp_issuer`` (optional but recommended): the IdP token's ``iss`` claim
      must equal this.

    For IdP key material you must provide either ``idp_public_key`` (single PEM)
    or ``idp_jwks_url`` (e.g. Google's JWKS — handles key rotation).

    **Offline by design — no revocation check.** Validation is purely local
    (signature, audience, expiry, ``idp_sub``/``svc`` bindings); the middleware
    does NOT call Duar to consult the token denylist or the user-deactivation
    flag. A deactivated user's already-issued authz token therefore stays accepted
    here until it expires naturally. Authz tokens are short-lived (default 5 min)
    to bound this window — keep ``AUTHZ_TOKEN_EXPIRE_MINUTES`` small. For
    revocation-sensitive operations, gate them with a Duar ``PermissionClient``
    / ``RoleClient`` call rather than relying on this middleware alone.

    **Auto-resolve (opt-in).** With ``auto_resolve=True`` a request that carries the
    IdP token and ``X-Workspace-Id`` but no ``X-Authz-Token`` has its authz token
    minted here via ``duar_instance.authz.resolve`` (service key, server-side) and
    cached per ``(idp_sub, workspace_id)`` for 80% of its TTL with single-flight
    minting. Lets scripts / Postman / Swagger call the API with one token. Requires
    ``duar_instance`` and ``idp_provider``. ``X-Authz-Token`` wins when both are sent.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        service_name: str,
        idp_audience: str | list[str],
        idp_public_key: str | None = None,
        idp_jwks_url: str | None = None,
        idp_issuer: str | None = None,
        duar_public_key: str | None = None,
        duar_instance: Duar | None = None,
        idp_algorithm: str = "RS256",
        duar_algorithm: str = "RS256",
        duar_audience: str = "duar:authz",
        exclude_paths: list[str] | None = None,
        idp_provider: str | None = None,
        auto_resolve: bool = False,
    ):
        super().__init__(app)
        if not service_name:
            raise ValueError("AuthzMiddleware requires service_name")
        if not idp_audience:
            raise ValueError("AuthzMiddleware requires idp_audience")
        if not duar_public_key and not duar_instance:
            raise ValueError(
                "AuthzMiddleware requires either duar_public_key or duar_instance for authz token verification"
            )
        if (
            not idp_public_key
            and not idp_jwks_url
            and not (duar_instance and (duar_instance.idp_jwks_url or duar_instance.idp_public_key))
        ):
            raise ValueError("AuthzMiddleware requires idp_public_key or idp_jwks_url for IdP token verification")
        if auto_resolve and duar_instance is None:
            raise ValueError("AuthzMiddleware auto_resolve requires duar_instance (it mints via the service key)")
        if auto_resolve and not idp_provider:
            raise ValueError("AuthzMiddleware auto_resolve requires idp_provider (e.g. 'google', 'entra_id')")

        self.service_name = service_name
        self.idp_audience = idp_audience
        self.idp_issuer = idp_issuer
        self._idp_public_key = idp_public_key
        self._idp_jwks_url = idp_jwks_url
        self._duar_public_key = duar_public_key
        self._duar_instance = duar_instance
        self.idp_algorithm = idp_algorithm
        self.duar_algorithm = duar_algorithm
        self.duar_audience = duar_audience
        self.exclude_paths = exclude_paths or ["/health", "/docs", "/openapi.json"]
        self.idp_provider = idp_provider
        self.auto_resolve = auto_resolve
        # "idp_sub|workspace_id" -> (authz_token, monotonic expiry). Insertion-ordered for eviction.
        self._resolve_cache: OrderedDict[str, tuple[str, float]] = OrderedDict()
        # In-flight mints keyed the same way, so concurrent misses share ONE Duar call
        # (Duar's /authz/resolve bucket is 60/min per service, shared with browser mints).
        self._resolve_pending: dict[str, asyncio.Task[str]] = {}

        jwks_url = idp_jwks_url or (duar_instance.idp_jwks_url if duar_instance else None)
        # The fetch is sync urllib (like the Duar one): dispatch runs it via
        # asyncio.to_thread, and the timeout bounds the worker-thread stall.
        self._idp_jwks_client: PyJWKClient | None = PyJWKClient(jwks_url, timeout=10) if jwks_url else None

        # Duar (authz token) key resolution. Static duar_public_key pins
        # one key (air-gapped); otherwise resolve by kid via PyJWKClient against
        # Duar's JWKS — same battle-tested path as IdP tokens, handles rotation.
        duar_jwks_url = (
            f"{duar_instance.base_url}/.well-known/jwks.json" if (duar_instance and not duar_public_key) else None
        )
        self._duar_jwk_client: PyJWKClient | None = PyJWKClient(duar_jwks_url, timeout=5) if duar_jwks_url else None

    @property
    def idp_public_key(self) -> str:
        if self._idp_public_key:
            return self._idp_public_key
        if self._duar_instance:
            return self._duar_instance.idp_public_key or ""
        return ""

    @property
    def duar_public_key(self) -> str:
        key = self._duar_public_key
        if not key and self._duar_instance:
            key = self._duar_instance.duar_public_key or ""
        if not key:
            raise RuntimeError(
                "Duar public key not available. Ensure duar_instance.lifespan() has run "
                "or provide duar_public_key directly."
            )
        return key

    @property
    def effective_scope(self) -> str:
        """The shared scope an incoming authz token's ``svc`` must match.

        A realm member resolves this from its Duar instance (discovered via
        ``whoami`` at startup); standalone services and static-key (air-gapped) mode
        fall back to ``service_name`` — today's behavior, unchanged.
        """
        if self._duar_instance is not None:
            # ponytail: getattr because _FakeDuar in test_authz_middleware.py predates this
            # attribute and cannot be edited (frozen test). Real Duar always has it post-A2.
            return getattr(self._duar_instance, "effective_scope", self.service_name)
        return self.service_name

    def _decode_idp_token(self, token: str) -> dict:
        """Decode and validate an IdP token.

        Enforces ``aud`` and ``iss`` — these are the sole defences against
        accepting a valid-but-wrong-client token from the same IdP.
        """
        decode_kwargs: dict = {
            "algorithms": [self.idp_algorithm],
            "audience": self.idp_audience,
        }
        if self.idp_issuer:
            decode_kwargs["issuer"] = self.idp_issuer

        if self._idp_jwks_client:
            signing_key = self._idp_jwks_client.get_signing_key_from_jwt(token)
            return jwt.decode(token, signing_key.key, **decode_kwargs)
        return jwt.decode(token, self.idp_public_key, **decode_kwargs)

    def _decode_authz(self, token: str) -> dict:
        """Verify a Duar authz token.

        Static ``duar_public_key`` mode pins one key (air-gapped, not
        rotation-capable). Otherwise the key is resolved by ``kid`` via
        ``PyJWKClient``, which refetches Duar's JWKS on a rotated-in kid.
        """
        key = self._duar_public_key
        if not key:
            key = self._duar_jwk_client.get_signing_key_from_jwt(token).key
        return jwt.decode(
            token,
            key,
            algorithms=[self.duar_algorithm],
            audience=self.duar_audience,
        )

    async def _auto_resolve(self, request: Request, idp_token: str, idp_payload: dict) -> str | Response:
        """Return an authz token for ``X-Workspace-Id`` — cached, or minted via Duar.

        Keyed by ``(idp_sub, workspace_id)``, not the IdP token: the token was verified
        locally just before this and the ``idp_sub`` binding check still runs on the
        result, so a script fetching a fresh IdP token per request reuses the cached
        mint instead of burning one Duar call per request.
        """
        raw_wid = request.headers.get("X-Workspace-Id")
        if not raw_wid:
            return JSONResponse(status_code=401, content={"detail": _MISSING_BOTH_DETAIL})
        try:
            workspace_id = str(uuid.UUID(raw_wid))
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Invalid X-Workspace-Id"})

        key = f"{idp_payload.get('sub')}|{workspace_id}"
        cached = self._resolve_cache.get(key)
        if cached is not None and cached[1] > time.monotonic():
            return cached[0]

        task = self._resolve_pending.get(key)
        if task is None:
            task = asyncio.ensure_future(self._mint(key, idp_token, workspace_id))
            self._resolve_pending[key] = task
            task.add_done_callback(lambda _t: self._resolve_pending.pop(key, None))
        try:
            # shield: one caller disconnecting must not cancel the mint the others await.
            return await asyncio.shield(task)
        except DuarError as exc:
            return self._mint_error(exc)
        except httpx.HTTPError:
            return JSONResponse(status_code=503, content={"detail": "Authorization service unavailable"})

    async def _mint(self, key: str, idp_token: str, workspace_id: str) -> str:
        data = await self._duar_instance.authz.resolve(idp_token, self.idp_provider, workspace_id)
        token: str = data["authz_token"]
        ttl = 0.8 * float(data.get("expires_in") or 300)  # re-mint well before it expires mid-flight
        self._resolve_cache[key] = (token, time.monotonic() + ttl)
        self._resolve_cache.move_to_end(key)
        while len(self._resolve_cache) > _RESOLVE_CACHE_MAX:
            self._resolve_cache.popitem(last=False)
        return token

    @staticmethod
    def _mint_error(exc: DuarError) -> JSONResponse:
        """Map a Duar /authz/resolve failure to the app's response (spec §1)."""
        if exc.status_code == 400:  # IdP token rejected (aud / signature / expiry)
            return JSONResponse(status_code=401, content={"detail": "IdP token rejected by Duar"})
        if exc.status_code in (403, 409):  # not a member / org not allowed / inactive / email conflict
            return JSONResponse(status_code=403, content={"detail": "Not authorized for this workspace"})
        if exc.status_code == 429:
            headers = {"Retry-After": exc.retry_after} if exc.retry_after else None
            return JSONResponse(
                status_code=429, content={"detail": "Authorization service rate limit"}, headers=headers
            )
        return JSONResponse(status_code=503, content={"detail": "Authorization service unavailable"})

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method == "OPTIONS":
            return await call_next(request)
        if any(request.url.path == p or request.url.path.startswith(p + "/") for p in self.exclude_paths):
            return await call_next(request)

        # 1. Extract IdP token from Authorization header
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"detail": "Missing IdP token"})
        idp_token = auth_header.removeprefix("Bearer ")

        # 2. Extract authz token. Without auto_resolve its absence is a hard 401 (unchanged).
        authz_token = request.headers.get("X-Authz-Token")
        if not authz_token and not self.auto_resolve:
            return JSONResponse(status_code=401, content={"detail": "Missing authz token"})

        # 3. Validate IdP token (signature + audience + optional issuer).
        #    Off-loop: the kid lookup can trigger a sync JWKS refetch.
        try:
            idp_payload = await asyncio.to_thread(self._decode_idp_token, idp_token)
        except jwt.ExpiredSignatureError:
            return JSONResponse(status_code=401, content={"detail": "IdP token expired"})
        except (jwt.InvalidTokenError, PyJWKClientError):
            return JSONResponse(status_code=401, content={"detail": "Invalid IdP token"})

        # 3b. Auto-resolve: no authz token but a target workspace — mint (or reuse) one.
        #     Runs after IdP validation so junk never consumes Duar's rate bucket.
        if not authz_token:
            resolved = await self._auto_resolve(request, idp_token, idp_payload)
            if isinstance(resolved, Response):
                return resolved
            authz_token = resolved

        # 4. Validate authz token (key selected by kid; supports rotation)
        try:
            authz_payload = await asyncio.to_thread(self._decode_authz, authz_token)
        except jwt.ExpiredSignatureError:
            return JSONResponse(status_code=401, content={"detail": "Authz token expired"})
        except (jwt.InvalidTokenError, PyJWKClientError):
            return JSONResponse(status_code=401, content={"detail": "Invalid authz token"})

        # 5. Verify binding: IdP sub must match authz idp_sub, both non-empty.
        idp_sub = idp_payload.get("sub")
        authz_idp_sub = authz_payload.get("idp_sub")
        if not idp_sub or not authz_idp_sub or idp_sub != authz_idp_sub:
            return JSONResponse(
                status_code=401,
                content={"detail": "Token binding mismatch: idp_sub does not match"},
            )

        # 6. Enforce svc binding: the authz token was minted for this service's
        #    effective scope (the realm slug for a member, else the service name).
        token_svc = authz_payload.get("svc")
        if not token_svc or token_svc != self.effective_scope:
            return JSONResponse(
                status_code=403,
                content={"detail": "Authz token was issued for a different service"},
            )

        # 7. Set user on request state
        try:
            request.state.user = AuthenticatedUser(
                user_id=uuid.UUID(authz_payload["sub"]),
                email=idp_payload.get("email", ""),
                name=idp_payload.get("name", ""),
                workspace_id=uuid.UUID(authz_payload["wid"]),
                workspace_slug=authz_payload.get("wslug", ""),
                workspace_role=authz_payload["wrole"],
                groups=[],
                org_id=uuid.UUID(authz_payload["oid"]) if authz_payload.get("oid") else None,
                org_slug=authz_payload.get("oslug"),
                org_is_public=bool(authz_payload.get("opub", False)),
            )
            request.state.token = authz_token
            request.state.idp_token = idp_token
        except (KeyError, ValueError):
            return JSONResponse(status_code=401, content={"detail": "Invalid token claims"})

        return await call_next(request)
