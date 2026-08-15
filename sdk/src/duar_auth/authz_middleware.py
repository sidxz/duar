"""Dual-token middleware for AuthZ mode.

Validates both an IdP token (identity) and a Duar authz token
(authorization), checking that the idp_sub claims match.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from duar_auth.types import AuthenticatedUser

if TYPE_CHECKING:
    from duar_auth.duar import Duar


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

        # 2. Extract authz token from X-Authz-Token header
        authz_token = request.headers.get("X-Authz-Token")
        if not authz_token:
            return JSONResponse(status_code=401, content={"detail": "Missing authz token"})

        # 3. Validate IdP token (signature + audience + optional issuer).
        #    Off-loop: the kid lookup can trigger a sync JWKS refetch.
        try:
            idp_payload = await asyncio.to_thread(self._decode_idp_token, idp_token)
        except jwt.ExpiredSignatureError:
            return JSONResponse(status_code=401, content={"detail": "IdP token expired"})
        except (jwt.InvalidTokenError, PyJWKClientError):
            return JSONResponse(status_code=401, content={"detail": "Invalid IdP token"})

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
