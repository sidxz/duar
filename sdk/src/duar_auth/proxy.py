"""Reverse-proxy router for private-network Duar deployments.

When Duar is not reachable from browsers (ClusterIP-only / internal overlay
network), the SDK's browser calls must route through the app backend. This
router forwards exactly the browser-facing surface and nothing else:

- ``POST /authz/resolve`` — workspace discovery AND authz-token mint. The
  service key is injected here, so this route doubles as the ``mintEndpoint``.
- ``GET /workspaces/{id}/members`` (+ ``q``/``limit``)
- ``GET /workspaces/{id}/groups``
- ``GET /workspaces/{id}/groups/{group_id}/members``
- ``GET /users/me``

The directory reads forward the caller's ``Authorization`` and ``X-Authz-Token``
headers untouched and deliberately do NOT attach the service key: Duar's
flexible auth ignores ``X-Authz-Token`` when a valid service key is present and
would try to decode the IdP bearer as a Duar token, failing with 401.

``X-Forwarded-For`` and ``User-Agent`` are passed through unchanged so
Duar's access logs and rate limiting still see real client IPs — set
``BEHIND_PROXY=true`` and ``TRUSTED_PROXY_COUNT`` to the number of
internet-facing proxies that append to XFF (typically 1: your ingress) on the
Duar deployment.

Usage::

    from duar_auth import Duar

    duar = Duar(...)
    app.include_router(duar.proxy_router(), prefix="/api/duar")

Frontend config then becomes::

    duarUrl: "/api/duar"
    mintEndpoint: "/api/duar/authz/resolve"
"""

from __future__ import annotations

import uuid

import httpx
from fastapi import APIRouter, Request, Response

_FORWARDED_HEADERS = ("authorization", "x-authz-token", "user-agent", "x-forwarded-for")


def create_proxy_router(base_url: str, service_key: str, timeout: float = 10.0) -> APIRouter:
    """Build an ``APIRouter`` that forwards the browser-facing Duar surface.

    Mount it under the same prefix the frontend uses as ``duarUrl``
    (e.g. ``app.include_router(router, prefix="/api/duar")``).
    """
    router = APIRouter(tags=["duar-proxy"])
    client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)

    def _headers(request: Request, *, with_service_key: bool) -> dict[str, str]:
        headers = {h: v for h in _FORWARDED_HEADERS if (v := request.headers.get(h))}
        if with_service_key:
            headers["X-Service-Key"] = service_key
            # The key is the credential on this path; the browser's bearer slot
            # (IdP token) must not shadow it upstream.
            headers.pop("authorization", None)
            headers.pop("x-authz-token", None)
        return headers

    async def _forward(request: Request, path: str, *, with_service_key: bool = False) -> Response:
        try:
            upstream = await client.request(
                request.method,
                path,
                params=request.query_params,
                content=await request.body() or None,
                headers={
                    **_headers(request, with_service_key=with_service_key),
                    **({"Content-Type": "application/json"} if request.method == "POST" else {}),
                },
            )
        except httpx.HTTPError:
            return Response(
                content=b'{"detail": "Duar is unreachable"}',
                status_code=502,
                media_type="application/json",
            )
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )

    @router.post("/authz/resolve")
    async def resolve(request: Request) -> Response:
        return await _forward(request, "/authz/resolve", with_service_key=True)

    @router.get("/workspaces/{workspace_id}/members")
    async def members(request: Request, workspace_id: uuid.UUID) -> Response:
        return await _forward(request, f"/workspaces/{workspace_id}/members")

    @router.get("/workspaces/{workspace_id}/groups")
    async def groups(request: Request, workspace_id: uuid.UUID) -> Response:
        return await _forward(request, f"/workspaces/{workspace_id}/groups")

    @router.get("/workspaces/{workspace_id}/groups/{group_id}/members")
    async def group_members(request: Request, workspace_id: uuid.UUID, group_id: uuid.UUID) -> Response:
        return await _forward(request, f"/workspaces/{workspace_id}/groups/{group_id}/members")

    @router.get("/users/me")
    async def profile(request: Request) -> Response:
        return await _forward(request, "/users/me")

    return router
