"""Team Notes — demo app showcasing the Duar SDK.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.config import duar, settings

app = FastAPI(
    title="Team Notes",
    description="Demo app showcasing the Duar SDK — "
    "workspace isolation, role enforcement, entity ACLs, and custom RBAC.",
    version="0.1.0",
    lifespan=duar.lifespan,
)

# CORS for the demo frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# JWT authentication
duar.protect(app, exclude_paths=["/health", "/docs", "/openapi.json", "/redoc"])

# Mount routes
from src.routes import router  # noqa: E402

app.include_router(router)


@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "service": "team-notes-demo"}


if __name__ == "__main__":
    import uvicorn

    print("\n" + "=" * 60)
    print("TEAM NOTES — Duar SDK Demo")
    print("=" * 60)
    print(f"\nDuar service: {settings.duar_url}")
    print(f"Demo backend:     http://localhost:{settings.port}")
    print(f"Demo frontend:    {settings.frontend_url}")
    print(f"\nAPI docs: http://localhost:{settings.port}/docs")
    print("=" * 60 + "\n")

    uvicorn.run("src.main:app", host=settings.host, port=settings.port, reload=True)
    
    
# ═══════════════════════════════════════════════════════════════════════════
# Without the autoconfig, you'd need to set up the lifespan, middleware, and RBAC registration manually like this:
# ═══════════════════════════════════════════════════════════════════════════
#
#   from contextlib import asynccontextmanager
#   from duar_auth.middleware import JWTAuthMiddleware
#   from duar_auth.permissions import PermissionClient
#   from duar_auth.roles import RoleClient
#
#   @asynccontextmanager
#   async def lifespan(app: FastAPI):
#       if not settings.service_api_key:
#           raise RuntimeError("SERVICE_API_KEY is required.")
#
#       # Create SDK clients
#       app.state.permissions = PermissionClient(
#           base_url=settings.duar_url,
#           service_name=settings.service_name,
#           service_key=settings.service_api_key,
#       )
#       app.state.roles = RoleClient(
#           base_url=settings.duar_url,
#           service_name=settings.service_name,
#           service_key=settings.service_api_key,
#       )
#
#       # Register RBAC actions (idempotent)
#       await app.state.roles.register_actions([
#           {"action": "notes:export", "description": "Export notes as JSON"},
#           {"action": "notes:bulk-delete", "description": "Bulk delete notes"},
#       ])
#
#       yield
#
#       await app.state.permissions.close()
#       await app.state.roles.close()
#
#   app = FastAPI(lifespan=lifespan)
#
#   app.add_middleware(
#       JWTAuthMiddleware,
#       base_url=settings.duar_url,
#       exclude_paths=["/health", "/docs", "/openapi.json", "/redoc"],
#       allowed_workspaces=set(settings.allowed_workspaces) or None,
#   )
#
# ═══════════════════════════════════════════════════════════════════════════

