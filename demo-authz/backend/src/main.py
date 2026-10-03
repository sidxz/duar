"""Team Notes (AuthZ Mode) — demo app showcasing Duar dual-token auth.

In this mode:
  1. The client app authenticates users directly with an IdP (e.g. Google)
  2. The client calls Duar's /authz/resolve with the IdP token
  3. Duar validates the IdP token and returns an authorization JWT
  4. The client sends BOTH tokens to this backend on every request
  5. AuthzMiddleware validates both tokens and checks idp_sub binding
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.config import duar, settings

if not settings.frontend_url or settings.frontend_url.strip() in {"", "*"}:
    raise RuntimeError(
        "frontend_url must be a concrete URL (set FRONTEND_URL in .env) — "
        "wildcards or empty values are unsafe with allow_credentials=True."
    )

app = FastAPI(
    title="Team Notes (AuthZ Mode)",
    description="Demo app showcasing Duar AuthZ mode — dual-token validation, "
    "workspace roles, entity ACLs, and custom RBAC.",
    version="0.1.0",
    lifespan=duar.lifespan,
)

# Dual-token authentication (IdP token + Duar authz token).
# /auth/mint is excluded: it's the login step, hit BEFORE the user has an
# authz token, so dual-token validation must not apply. The mint route itself
# proxies to Duar's /authz/resolve using the backend's service key.
duar.protect(
    app,
    exclude_paths=[
        "/health",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/auth/mint",
    ],
)

# CORS must be added AFTER auth middleware so it wraps it (outermost).
# Methods and headers are listed explicitly rather than using ``*`` — combined
# with ``allow_credentials=True`` wildcards grant more than intended and train
# copy-paste callers into unsafe patterns.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Authz-Token"],
)

# Mount routes
from src.auth_routes import router as auth_router  # noqa: E402
from src.routes import router  # noqa: E402

app.include_router(auth_router)
app.include_router(router)


@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "service": "team-notes-authz-demo"}


if __name__ == "__main__":
    import uvicorn

    print("\n" + "=" * 60)
    print("TEAM NOTES — Duar AuthZ Mode Demo")
    print("=" * 60)
    print(f"\nDuar service: {settings.duar_url}")
    print(f"Demo backend:     http://localhost:{settings.port}")
    print(f"Demo frontend:    {settings.frontend_url}")
    print(f"\nMode: authz (dual-token)")
    print(f"  - IdP token:   Authorization: Bearer <idp_token>")
    print(f"  - Authz token: X-Authz-Token: <authz_token>")
    print(f"\nAPI docs: http://localhost:{settings.port}/docs")
    print("=" * 60 + "\n")

    uvicorn.run("src.main:app", host=settings.host, port=settings.port, reload=True)
