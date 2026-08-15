# Quickstart

End-to-end walkthrough: configure Google OAuth, register your apps in Sentinel, and build a minimal backend + frontend that authenticates users via AuthZ mode.

**Assumes you have already run `make setup && make start`.** If not, see [Getting Started](index.md).

## 1. Configure Google OAuth

1. Go to the [Google Cloud Console](https://console.cloud.google.com/apis/credentials).
2. Create an **OAuth 2.0 Client ID** (application type: **Web application**).
3. Add to **Authorized JavaScript origins**: `http://localhost:5173` (your frontend dev server).
4. Add to **Authorized redirect URIs**: `http://localhost:9003/auth/callback/google` (for admin panel login).
5. Copy the credentials into `service/.env`:

```dotenv
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
ADMIN_EMAILS=you@example.com
```

Restart the service to pick up the changes:

```bash
# Ctrl+C the running service, then:
make start
```

## 2. Open the Admin Panel

In a separate terminal:

```bash
make admin
```

Open [http://localhost:9004](http://localhost:9004) and sign in with Google. Your email must be in `ADMIN_EMAILS` or you will be denied access.

## 3. Register a Service App

Your backend needs a service API key and your frontend needs its origin allowlisted.

1. In the admin panel, go to **Service Apps**.
2. Click **Register Service App**.
3. Set name and service name (e.g. `my-app`).
4. Add `http://localhost:5173` to **Allowed Origins**.
5. Save and copy the generated `sk_...` key.

The service key is for backend-to-Sentinel calls. The allowed origin lets your frontend call Sentinel's `/authz/resolve` endpoint directly from the browser.

## 4. Install the SDKs

Backend (Python):

```bash
pip install sentinel-auth-sdk
```

Frontend (React):

```bash
npm install @sentinel-auth/js @sentinel-auth/react
```

## 5. Build the Backend

A minimal FastAPI app protected by Sentinel in AuthZ mode:

```python
# app.py
import os
from fastapi import FastAPI, Depends
from sentinel_auth import Sentinel

sentinel = Sentinel(
    base_url="http://localhost:9003",
    service_name="my-app",
    service_key=os.environ["SENTINEL_SERVICE_KEY"],
    mode="authz",
    idp_jwks_url="https://www.googleapis.com/oauth2/v3/certs",
    idp_audience="123-abc.apps.googleusercontent.com",  # your OAuth client_id
)

app = FastAPI(lifespan=sentinel.lifespan)
sentinel.protect(app)

@app.get("/api/me")
async def me(user=Depends(sentinel.require_user)):
    return {
        "user_id": user.user_id,
        "email": user.email,
        "workspace_id": user.workspace_id,
        "role": user.workspace_role,
    }
```

Key points:

- `mode="authz"` -- the frontend authenticates users directly with Google. Sentinel issues a short-lived authz JWT with workspace roles.
- `idp_jwks_url` -- the middleware validates Google ID tokens against Google's public keys. This handles key rotation automatically.
- `sentinel.lifespan` -- on startup, fetches Sentinel's public key from its JWKS endpoint for authz token validation.
- `sentinel.protect(app)` -- adds `AuthzMiddleware` which validates both the IdP token (`Authorization` header) and the authz token (`X-Authz-Token` header) on every request.
- `sentinel.require_user` -- FastAPI dependency that extracts the authenticated user from the validated tokens.

Run it:

```bash
SENTINEL_SERVICE_KEY=sk_your_key uvicorn app:app --port 8000 --reload
```

## 6. Build the Frontend

A minimal React app with Google Sign-In and Sentinel AuthZ:

```tsx
// src/App.tsx
import { AuthzProvider, AuthzCallback, useAuthz } from '@sentinel-auth/react'
import { IdpConfigs, AuthzLocalStorageStore } from '@sentinel-auth/js'
import { BrowserRouter, Routes, Route } from 'react-router-dom'

const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID

function App() {
  return (
    <BrowserRouter>
      <AuthzProvider config={{
        sentinelUrl: 'http://localhost:9003',
        idps: { google: IdpConfigs.google(GOOGLE_CLIENT_ID) },
        storage: new AuthzLocalStorageStore(),
      }}>
        <Routes>
          <Route path="/auth/callback" element={<AuthzCallback />} />
          <Route path="/" element={<Home />} />
        </Routes>
      </AuthzProvider>
    </BrowserRouter>
  )
}

function Home() {
  const { user, isAuthenticated, login, logout, fetchJson } = useAuthz()

  if (!isAuthenticated) {
    return <button onClick={() => login('google')}>Sign in with Google</button>
  }

  const handleMe = async () => {
    const data = await fetchJson('http://localhost:8000/api/me')
    console.log(data)
  }

  return (
    <div>
      <p>Signed in as {user?.email} ({user?.workspaceRole})</p>
      <button onClick={handleMe}>Call /api/me</button>
      <button onClick={logout}>Sign out</button>
    </div>
  )
}

export default App
```

Key points:

- `IdpConfigs.google(clientId)` -- preconfigured Google OAuth settings (authorization URL, scopes, response type).
- `AuthzLocalStorageStore` -- persists tokens across page reloads. Use `AuthzMemoryStore` (default) for session-only storage.
- `login('google')` -- redirects to Google's consent screen. After sign-in, Google redirects back to `/auth/callback` with an ID token.
- `AuthzCallback` -- handles the OAuth callback, calls `POST /authz/resolve` to exchange the ID token for a Sentinel authz JWT, and prompts workspace selection if the user belongs to multiple workspaces.
- `fetchJson` -- wraps `fetch` with automatic dual-token headers (`Authorization: Bearer <idp_token>` + `X-Authz-Token: <authz_token>`) and transparent token refresh.

Set up the frontend:

```bash
# Create a Vite React project (or use your own)
npm create vite@latest my-frontend -- --template react-ts
cd my-frontend
npm install @sentinel-auth/js @sentinel-auth/react react-router-dom

# Set your Google client ID
echo "VITE_GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com" > .env

npm run dev
```

## 7. Test the Flow

1. Open `http://localhost:5173`.
2. Click **Sign in with Google**.
3. Google redirects back with an ID token.
4. Sentinel validates the token, provisions the user (first login), and returns available workspaces.
5. After workspace selection, Sentinel issues an authz JWT.
6. Click **Call /api/me** -- the backend validates both tokens and returns your user info.

```
Browser                    Google                Sentinel (:9003)         Backend (:8000)
-------                    ------                ----------------         ---------------
login('google')  --------> Consent screen
                 <-------- ID token (via redirect)

POST /authz/resolve  ---------------------------> Validate ID token
  { idp_token, provider }                          JIT provision user
                     <---------------------------- { workspaces }

POST /authz/resolve  ---------------------------> Issue authz JWT
  { ..., workspace_id }
                     <---------------------------- { authz_token }

GET /api/me  -----------------------------------------------------------> AuthzMiddleware
  Authorization: Bearer <idp_token>                                        validates both
  X-Authz-Token: <authz_token>                                            tokens, checks
                 <-------------------------------------------------------- { user info }
```

## 8. Next Steps

- Review all settings in the [Configuration](configuration.md) reference.
- Read [How It Works](../guide/how-it-works.md) for the full architecture.
- Follow the [Tutorial](../tutorial/react.md) to build a complete app with RBAC and entity ACLs.
- Explore the [Python SDK](../sdk/index.md) and [JS/TS SDK](../js-sdk/index.md) references.
