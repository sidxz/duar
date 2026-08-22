# Path-Prefix Deployment

Duar can be served under a path prefix instead of a host root — typical on a
shared Kubernetes ingress (AKS, nginx-ingress, Application Gateway):

- service: `https://apps.example.org/duar`
- admin: `https://apps.example.org/duar-admin`

Neither image is prefix-aware at request time. The ingress **strips the prefix**
before forwarding, and three settings tell Duar its public URLs. The published
images are used as-is — no rebuild.

## 1. Ingress: route and strip the prefix

The service must receive `/auth/callback/google`, not `/duar/auth/callback/google`.

=== "nginx-ingress"

    ```yaml
    apiVersion: networking.k8s.io/v1
    kind: Ingress
    metadata:
      name: duar
      annotations:
        nginx.ingress.kubernetes.io/use-regex: "true"
        nginx.ingress.kubernetes.io/rewrite-target: /$2
    spec:
      rules:
        - host: apps.example.org
          http:
            paths:
              - path: /duar(/|$)(.*)
                pathType: ImplementationSpecific
                backend: { service: { name: duar, port: { number: 9003 } } }
              - path: /duar-admin(/|$)(.*)
                pathType: ImplementationSpecific
                backend: { service: { name: duar-admin, port: { number: 80 } } }
    ```

=== "Application Gateway (AGIC)"

    One Ingress per prefix, each with
    `appgw.ingress.kubernetes.io/backend-path-prefix: "/"` so the prefix is
    replaced by `/` before reaching the pod.

Forward `Host` and `X-Forwarded-Proto` as usual (ingress controllers do by default).

## 2. Service

```env
BASE_URL=https://apps.example.org/duar
ADMIN_URL=https://apps.example.org/duar-admin
BEHIND_PROXY=true
COOKIE_SECURE=true
```

- IdP redirect URIs are built from `BASE_URL`, so register
  `https://apps.example.org/duar/auth/callback/<provider>` and
  `https://apps.example.org/duar/auth/admin/callback/<provider>` with each IdP.
- JWT `iss` is `BASE_URL`, prefix included.
- `ALLOWED_HOSTS` is derived from the hostnames of both URLs — one host, nothing
  extra to set. Trailing slashes are stripped.

## 3. Admin

```env
DUAR_ADMIN_BASE_PATH=/duar-admin
DUAR_BACKEND=duar:9003
```

Applied when the container starts (it rewrites `<base href>` in `index.html`).
The admin reaches the service through its own nginx at `/duar-admin/api/*`, so it
needs no CORS entry and never sees `BASE_URL`. The `admin_token` cookie is
host-scoped (`Path=/`), which covers both prefixes.

## 4. Client apps and SDKs

Point SDKs at the prefixed URL; every path is appended verbatim:

- Python: `Duar(base_url="https://apps.example.org/duar", ...)`
- JS / React: `duarUrl: 'https://apps.example.org/duar'`
- JWKS: `https://apps.example.org/duar/.well-known/jwks.json`

The Next.js middlewares derive the expected `iss` from `jwksUrl` / `duarUrl`
with the prefix kept. Releases up to 1.0.0 defaulted to the origin only — on
those, pass `issuer: 'https://apps.example.org/duar'` explicitly. The Python
SDK does not verify `iss`.

## Verify

```bash
curl https://apps.example.org/duar/health                      # {"status":"ok"}
curl https://apps.example.org/duar/.well-known/jwks.json       # keys
curl -s https://apps.example.org/duar-admin/ | grep '<base'    # <base href="/duar-admin/" />
curl https://apps.example.org/duar-admin/api/health            # service, via the admin's nginx
curl -sD - -o /dev/null https://apps.example.org/duar/auth/admin/login/google | grep -i location
# → IdP URL whose redirect_uri starts with https://apps.example.org/duar/
```
