"""Starlette/FastAPI middleware for JWT access token validation.

Add this middleware to your FastAPI app to automatically validate
JWT tokens on incoming requests and populate ``request.state.user``
with an ``AuthenticatedUser`` instance.
"""

import asyncio
import uuid

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from duar_auth._utils import warn_if_insecure
from duar_auth.types import AuthenticatedUser


class JWTAuthMiddleware(BaseHTTPMiddleware):
    """Validates JWT access tokens and sets ``request.state.user``.

    For each incoming request (except excluded paths), this middleware:

    1. Extracts the ``Authorization: Bearer <token>`` header
    2. Decodes and validates the JWT using the provided public key
    3. Sets ``request.state.user`` to an ``AuthenticatedUser`` instance
    4. Returns 401 if the token is missing, expired, or invalid

    Provide **one of** ``base_url``, ``jwks_url``, or ``public_key`` to
    specify how the signing key is obtained.

    Args:
        app: The ASGI application to wrap.
        base_url: Root URL of the Duar identity service (e.g.
            ``"http://localhost:9003"``). The JWKS endpoint is derived
            automatically as ``{base_url}/.well-known/jwks.json``.
            This is the recommended option.
        public_key: RSA public key (PEM format) used to verify JWT signatures.
            Use this for air-gapped deployments where the service cannot
            reach the identity service at runtime.
        jwks_url: Explicit JWKS endpoint URL. Use this only when pointing
            at a non-Duar OIDC provider whose JWKS path differs from
            the standard ``/.well-known/jwks.json``.
        algorithm: JWT signing algorithm. Defaults to ``"RS256"``.
        audience: Expected JWT audience claim. Defaults to ``"duar:access"``.
        exclude_paths: List of path prefixes to skip authentication for.
            Defaults to ``["/health", "/docs", "/openapi.json"]``.
        allowed_workspaces: Optional set of workspace IDs (as strings) that
            are permitted to access this service. ``None`` (default) allows
            all workspaces. Returns 403 if the JWT's workspace is not in the set.

    Example using base_url (recommended)::

        app.add_middleware(
            JWTAuthMiddleware,
            base_url="http://localhost:9003",
        )

    Example using PEM file::

        from pathlib import Path

        app.add_middleware(
            JWTAuthMiddleware,
            public_key=Path("keys/public.pem").read_text(),
        )
    """

    def __init__(
        self,
        app: ASGIApp,
        base_url: str | None = None,
        public_key: str | None = None,
        jwks_url: str | None = None,
        algorithm: str = "RS256",
        audience: str = "duar:access",
        exclude_paths: list[str] | None = None,
        allowed_workspaces: set[str] | None = None,
    ):
        super().__init__(app)
        if base_url:
            jwks_url = f"{base_url.rstrip('/')}/.well-known/jwks.json"
        if not public_key and not jwks_url:
            raise ValueError("Provide base_url, jwks_url, or public_key")
        self.public_key = public_key
        self.jwks_url = jwks_url
        self.algorithm = algorithm
        self.audience = audience
        self.exclude_paths = exclude_paths or ["/health", "/docs", "/openapi.json"]
        self.allowed_workspaces = allowed_workspaces
        # Resolve the signing key by kid via PyJWKClient — it caches the JWKS and
        # refetches on a rotated-in kid, the same battle-tested path used for IdP
        # tokens. Static public_key mode pins one key (air-gapped, no rotation).
        self._jwk_client = PyJWKClient(jwks_url, timeout=5) if jwks_url and not public_key else None
        if jwks_url:
            warn_if_insecure(jwks_url, "JWTAuthMiddleware")

    def _signing_key(self, token: str):
        """Return the verifying key: the pinned PEM in static mode, otherwise the
        JWKS key whose kid matches the token (PyJWKClient refetches on miss)."""
        if self.public_key:
            return self.public_key
        return self._jwk_client.get_signing_key_from_jwt(token).key

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Skip auth for excluded paths (exact match or path prefix with boundary)
        if any(request.url.path == p or request.url.path.startswith(p + "/") for p in self.exclude_paths):
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid Authorization header"},
            )

        token = auth_header.removeprefix("Bearer ")
        try:
            # PyJWKClient's JWKS fetch is sync urllib — off-loop so a cache-expiry
            # or rotated-kid refetch can't stall every in-flight request.
            key = await asyncio.to_thread(self._signing_key, token)
            payload = jwt.decode(token, key, algorithms=[self.algorithm], audience=self.audience)
        except jwt.ExpiredSignatureError:
            return JSONResponse(status_code=401, content={"detail": "Token has expired"})
        except PyJWKClientConnectionError:
            return JSONResponse(status_code=500, content={"detail": "Authentication service unavailable"})
        except (jwt.InvalidTokenError, PyJWKClientError):
            return JSONResponse(status_code=401, content={"detail": "Invalid token"})
        except Exception:
            return JSONResponse(status_code=500, content={"detail": "Authentication service unavailable"})

        try:
            if self.allowed_workspaces is not None and payload["wid"] not in self.allowed_workspaces:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Workspace not permitted for this service"},
                )

            request.state.token = token
            request.state.user = AuthenticatedUser(
                user_id=uuid.UUID(payload["sub"]),
                email=payload["email"],
                name=payload["name"],
                workspace_id=uuid.UUID(payload["wid"]),
                workspace_slug=payload["wslug"],
                workspace_role=payload["wrole"],
                groups=[uuid.UUID(g) for g in payload.get("groups", [])],
                org_id=uuid.UUID(payload["oid"]) if payload.get("oid") else None,
                org_slug=payload.get("oslug"),
                org_is_public=bool(payload.get("opub", False)),
            )
        except (KeyError, ValueError):
            return JSONResponse(status_code=401, content={"detail": "Invalid token claims"})

        return await call_next(request)
