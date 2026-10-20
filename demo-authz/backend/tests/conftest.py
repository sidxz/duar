"""Settings the demo requires at import time (no live Duar or IdP is contacted)."""

import os

os.environ.setdefault("IDP_AUDIENCE", "test-client.apps.googleusercontent.com")
os.environ.setdefault("SERVICE_API_KEY", "sk_test")
os.environ.setdefault("FRONTEND_URL", "http://localhost:9201")
