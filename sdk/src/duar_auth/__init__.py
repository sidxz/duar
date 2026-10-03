from importlib.metadata import version

from duar_auth.auth import RequestAuth, SystemAuth
from duar_auth.authz import AuthzClient
from duar_auth.authz_middleware import AuthzMiddleware
from duar_auth.dependencies import get_token
from duar_auth.duar import Duar
from duar_auth.middleware import JWTAuthMiddleware
from duar_auth.permissions import PermissionClient
from duar_auth.proxy import create_proxy_router
from duar_auth.roles import RoleClient
from duar_auth.types import AuthenticatedUser, DuarError, WorkspaceContext

__version__ = version("duar-auth")
__all__ = [
    "AuthenticatedUser",
    "AuthzClient",
    "AuthzMiddleware",
    "JWTAuthMiddleware",
    "PermissionClient",
    "RequestAuth",
    "RoleClient",
    "Duar",
    "DuarError",
    "SystemAuth",
    "WorkspaceContext",
    "__version__",
    "create_proxy_router",
    "get_token",
]
