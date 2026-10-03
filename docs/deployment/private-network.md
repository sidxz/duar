# Private-Network Deployment

Duar does not need a public address. In authz mode, user login happens at
the IdP (Google/EntraID), token minting already routes through your backend,
and the SDKs ship reverse-proxy helpers for the remaining browser-facing reads
— so Duar can live on a cluster-internal network reachable only by your app
backends.

```
Browser ──▶ IdP (Google/EntraID) ──▶ back to app origin with id_token
Browser ──▶ App backend /api/duar/* ──▶ Duar (internal)   ← proxy helpers
App backend ──▶ Duar (permissions, roles, m2m, JWKS)          ← already internal
```

## Duar service

- **Kubernetes**: ClusterIP Service, no Ingress. Add a NetworkPolicy allowing
  ingress only from app-backend pods. **Docker Swarm**: attach Duar to an
  internal overlay network; publish no ports.
- Run a single listener with `TIER=all`. The public/internal tier split guards
  a public socket; with nothing published, the network boundary does that job.
- Set `BASE_URL` to the internal DNS name (e.g. `http://duar.auth.svc:9003`).
  `BASE_URL` is the JWT `iss` claim — app-side verifiers must use the same value.
- Duar still needs **outbound** HTTPS to the IdPs (JWKS, token exchange).

## App backends: mount the proxy

Each app forwards exactly the browser-facing surface — discovery/mint via
`POST /authz/resolve` (service key injected server-side) plus the read-only
directory endpoints (members, groups, group members, `/users/me`) with the
caller's tokens passed through. Nothing else is reachable.

FastAPI (Python SDK):

```python
app.include_router(duar.proxy_router(), prefix="/api/duar")
```

Next.js — `app/api/duar/[...path]/route.ts`:

```ts
import { createDuarProxy } from '@duar-auth/nextjs/proxy'

export const { GET, POST } = createDuarProxy({
  duarUrl: process.env.DUAR_URL!,        // internal URL
  serviceKey: process.env.DUAR_SERVICE_KEY!,
})
```

## Frontends

```ts
const authz = new DuarAuthz({
  duarUrl: '/api/duar',                    // same-origin proxy mount
  mintEndpoint: '/api/duar/authz/resolve',     // the proxy IS the mint endpoint
  // ...idps, storage as usual
})
```

The Next.js Edge middleware keeps the **internal** URL — it verifies tokens
server-side and derives JWKS/issuer from it:

```ts
createDuarAuthzMiddleware({ duarUrl: process.env.DUAR_URL!, ... })
```

## Preserving client IPs in logs and rate limits

The proxy helpers forward `X-Forwarded-For` and `User-Agent` unchanged. For
Duar's access logs, security events, and per-IP rate limits to see real
client IPs, set on Duar:

```env
BEHIND_PROXY=true
TRUSTED_PROXY_COUNT=1   # proxies that APPEND to XFF — typically just your ingress
```

The app-backend hop passes XFF through without appending, so it does not count.

## Caveats

- **GitHub as IdP is unsupported** in this topology — its proxy-login flow
  needs a browser-reachable Duar. Google/EntraID implicit flows are
  unaffected: the browser talks to the IdP and returns to **your app's** origin,
  so the IdP never needs a route to Duar. Only Duar's outbound JWKS
  fetch does — allow egress to `login.microsoftonline.com` (Entra) or
  `www.googleapis.com` (Google) in your NetworkPolicy / Azure Firewall.
- **Admin panel** is internal-only (a feature): reach it via VPN, jumpbox, or
  `kubectl port-forward`. Note that admin OAuth builds its redirect URI as
  `{BASE_URL}/auth/admin/callback/{provider}` — with `BASE_URL` set to internal
  DNS, the IdP bounces the browser to a host it cannot resolve. Either make the
  admin's local address answer at exactly that host and port (hosts-file entry +
  `kubectl port-forward`, and register that URI with the IdP), or give the admin
  surface its own restricted ingress.
- **`/authz/resolve` rate limit** is keyed by service key, so one app's entire
  login+refresh volume shares a bucket — size `RATE_LIMIT_AUTHZ_RESOLVE`
  accordingly.
