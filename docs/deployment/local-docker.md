# Run Locally with Docker (Entra ID)

Run Duar + the admin panel on your laptop from the published images. Sign-in is through a Microsoft Entra ID app registration you
create yourself. ~15 minutes.

You need: Docker Desktop (or Docker Engine + Compose v2), `openssl`, and rights to
create an app registration in your Entra tenant.

## 1. Entra ID app registration

Azure Portal → **Microsoft Entra ID → App registrations → New registration**:

| Setting | Value |
|---|---|
| Name | `duar-local` (anything) |
| Supported account types | **Accounts in this organizational directory only** (single tenant — Duar does not accept `common`/`organizations` issuers) |
| Redirect URI | Platform **Web** → `http://localhost:9003/auth/admin/callback/entra_id` |

After it is created:

1. **Authentication → Web → Add URI**: `http://localhost:9003/auth/callback/entra_id` (used by app logins; the first one is the admin panel). Save.
2. **Certificates & secrets → New client secret** → copy the **Value** now (shown once).
3. **Overview** → copy **Application (client) ID** and **Directory (tenant) ID**.
4. **Token configuration → Add optional claim → ID** → tick `email` (accept the prompt to add the Graph `email` permission). Add `xms_edov` too if it is listed; if not, add `{"name": "xms_edov"}` under `optionalClaims.idToken` in **Manifest**. Without `email`, work accounts fall back to `preferred_username`; without `xms_edov`, Duar trusts the tenant pin (fine locally).

`http://` redirect URIs are only allowed by Entra for `localhost` — that is exactly what we want here.

## 2. Local files

```bash
mkdir -p duar-local/keys && cd duar-local
openssl genrsa -out keys/private.pem 2048
openssl rsa -in keys/private.pem -pubout -out keys/public.pem
```

`.env` (fill in the three Entra values and your email):

```bash
POSTGRES_PASSWORD=duar
REDIS_PASSWORD=duar
SESSION_SECRET_KEY=<output of: openssl rand -hex 32>

ENTRA_CLIENT_ID=<Application (client) ID>
ENTRA_CLIENT_SECRET=<client secret value>
ENTRA_TENANT_ID=<Directory (tenant) ID>

# Your Entra sign-in address — auto-promoted to admin on first login
ADMIN_EMAILS=you@yourcompany.com
```

`docker-compose.yml`:

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: duar
      POSTGRES_USER: duar
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes: [pg_data:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U duar"]
      interval: 5s
      retries: 10

  redis:
    image: redis:7-alpine
    command: redis-server --requirepass ${REDIS_PASSWORD}
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD}", "ping"]
      interval: 5s
      retries: 10

  duar:
    image: ghcr.io/sidxz/duar:0.20.1
    environment:
      DATABASE_URL: postgresql+asyncpg://duar:${POSTGRES_PASSWORD}@postgres:5432/duar
      REDIS_URL: redis://:${REDIS_PASSWORD}@redis:6379/0
      BASE_URL: http://localhost:9003
      ADMIN_URL: http://localhost:9004
      SESSION_SECRET_KEY: ${SESSION_SECRET_KEY}
      JWT_PRIVATE_KEY_PATH: /keys/private.pem
      JWT_PUBLIC_KEY_PATH: /keys/public.pem
      ENTRA_CLIENT_ID: ${ENTRA_CLIENT_ID}
      ENTRA_CLIENT_SECRET: ${ENTRA_CLIENT_SECRET}
      ENTRA_TENANT_ID: ${ENTRA_TENANT_ID}
      ADMIN_EMAILS: ${ADMIN_EMAILS}
      DEBUG: "true"            # local only: plain http cookies, non-TLS Redis, /docs enabled
      LOG_FORMAT: console
    volumes: [./keys:/keys:ro]
    ports: ["9003:9003"]
    depends_on:
      postgres: { condition: service_healthy }
      redis: { condition: service_healthy }

  duar-admin:
    image: ghcr.io/sidxz/duar-admin:0.20.1
    environment:
      DUAR_BACKEND: duar:9003
    ports: ["9004:80"]
    depends_on: [duar]

volumes:
  pg_data:
```

## 3. Run

```bash
docker compose up -d
docker compose logs -f duar      # migrations run automatically on first start
```

Then:

- <http://localhost:9003/health> → `{"status":"ok"}`
- <http://localhost:9003/auth/providers> → should list `entra_id`
- <http://localhost:9004> → admin panel → **Sign in with Entra ID** → you land on the dashboard as an admin (because of `ADMIN_EMAILS`).
- <http://localhost:9003/docs> → interactive API docs (`DEBUG=true` only).

## Troubleshooting

| Symptom | Fix |
|---|---|
| **Sign in with Entra ID** returns a 500 | `ENTRA_TENANT_ID` is wrong — Duar's OIDC discovery `https://login.microsoftonline.com/<tenant>/v2.0/.well-known/openid-configuration` returned 400. |
| `AADSTS50011` redirect URI mismatch | The URI in Entra must be exactly `http://localhost:9003/auth/admin/callback/entra_id` under the **Web** platform (not SPA). |
| `AADSTS7000215` invalid client secret | You copied the secret *ID*, not the *Value*. Create a new secret. |
| Redirected back to `/login?error=not_admin` | Your sign-in address is not in `ADMIN_EMAILS` (or `email` claim differs from what you expected — check the Entra token; add the `email` optional claim). |
| `/login?error=no_email_claim` | Add the `email` optional claim (step 1.4). |
| Duar logs a permission error reading `/keys/private.pem` | On Linux hosts the container runs as uid 1000: `chmod 644 keys/private.pem` (local dev key only). |
| Providers list is empty | Both `ENTRA_CLIENT_ID` and `ENTRA_CLIENT_SECRET` must be set (`ENTRA_TENANT_ID` too). |

Reset everything: `docker compose down -v`.

## Next

The tenant pin means only accounts from *your* tenant can sign in. To let a real
app authenticate against this instance, register it as a Login App in the admin
panel and follow the [Quickstart](../getting-started/quickstart.md). For a
hardened deployment see [Deployment](index.md).
