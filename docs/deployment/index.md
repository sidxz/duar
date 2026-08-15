# Deployment

Duar is a stateless FastAPI application. Multiple instances can run behind a load balancer as long as they share the same PostgreSQL database, Redis instance, and JWT signing keys.

## Architecture

```
                ┌─────────────┐
                │  Reverse    │
  HTTPS ───────>│  Proxy      │
                │  (Caddy)    │
                └──────┬──────┘
                       │ HTTP :9003
                ┌──────v──────┐
                │  Duar   │
                │  Auth       │
                └──┬──────┬───┘
                   │      │
          ┌────────v┐  ┌──v────────┐
          │ Postgres │  │   Redis   │
          │   :5432  │  │   :6379   │
          └──────────┘  └───────────┘
```

---

## Development (Docker Compose)

The default `docker-compose.yml` starts PostgreSQL 16 and Redis 7 with TLS and health checks:

```bash
make setup    # Generate keys, TLS certs, .env files, install deps, start containers
make start    # Start Duar on :9003 (auto-migrates)
make admin    # Start admin panel on :9004
```

`make setup` handles everything:

- Generates RS256 JWT signing keys (`keys/`)
- Generates TLS certs for Postgres and Redis (`keys/tls/`) using a self-signed CA
- Creates `service/.env` with a random `SESSION_SECRET_KEY`
- Creates `.env.prod` with random database and Redis passwords
- Installs Python and Node dependencies
- Starts database containers

### Container Ports

| Service | Container Port | Host Port |
|---------|---------------|-----------|
| PostgreSQL | 5432 | 9001 |
| Redis | 6379 | 9002 |
| Duar | 9003 | 9003 |

### Data Persistence

PostgreSQL data is stored in the `identity_pg_data` Docker volume. To wipe everything:

```bash
make clean    # Stop containers and delete volumes
make nuke     # Full reset: volumes + keys + env files + deps
```

---

## Production Docker Compose

The production compose file (`docker-compose.prod.yml`) uses `.env.prod` for configuration. Generate it with `make setup`, then edit:

```bash
vim .env.prod   # Set BASE_URL, ADMIN_URL, OAuth creds, ADMIN_EMAILS
```

Deploy:

```bash
docker stack deploy -c docker-compose.prod.yml duar
```

Both PostgreSQL and Redis run with TLS enabled. The service connects via `?ssl=require` (Postgres) and `rediss://` (Redis).

---

## Network Split (Public / Internal Listeners) { #network-split-public--internal-listeners }

The entire service-key surface — `/realm/*`, `/permissions/*`, `/authz/resolve`, and the service-facing `/roles/*` — can be moved onto an **unpublished internal listener** that the public internet has no socket to. This is a structural isolation: there is no proxy rule whose failure re-exposes those routes.

One image, two processes selected by the `TIER` environment variable:

| `TIER` | Port | Published? | Mounts |
|--------|------|-----------|--------|
| `public` | 9003 | yes | admin, org-admin, auth (OAuth proxy), client-log, user/workspace/group, JWKS |
| `internal` | 9010 | **no** (overlay-only) | realm, permissions, authz, roles |
| `all` *(default)* | 9003 | yes | everything — the single combined app |

`TIER` is unset (`all`) for development, `make start`, tests, and small single-process deployments — **non-breaking**. Production opts into the split by running two services from the same image.

The internal listener drops the Session and CORS middleware (it has no browser callers) and keeps SecurityHeaders, rate limiting, request-context, and access logging. JWKS stays on the public listener by default (public keys are meant to be published).

### Swarm topology

`docker-compose.prod.yml` defines both services on the `duar` overlay:

- **`duar`** — `TIER=public`, published on `:9003`, serves humans and the admin panel.
- **`duar-internal`** — `TIER=internal`, command runs uvicorn on `:9010`, **no `ports:` mapping** (unpublished by design), reachable only as `http://duar-internal:9010` on the overlay. It sets `SESSION_SECRET_KEY=""` and `CORS_ORIGINS=""` (the dropped middleware needs neither) and `depends_on` the public service so the schema is migrated first.

```bash
docker stack deploy -c docker-compose.prod.yml duar
```

### Pointing apps at the internal listener

A backend that holds a Duar **service key** points its SDK `base_url` at the internal listener:

```python
duar = Duar(base_url="http://duar-internal:9010", service_name="reports", service_key=...)
```

```typescript
const m2m = new M2mTokenClient('http://duar-internal:9010', process.env.SERVICE_KEY)
```

Browser-facing flows (login, the admin panel) continue to use the public `:9003` URL. See [Realms](../guide/realms.md) for what runs over this surface.

---

## Production Checklist

### TLS

- [ ] Reverse proxy (nginx, Caddy, ALB) handles TLS termination
- [ ] `BEHIND_PROXY=true` so rate limiting reads `X-Forwarded-For`
- [ ] PostgreSQL connection uses `?ssl=require`
- [ ] Redis uses `rediss://` with `REDIS_TLS_VERIFY=required`
- [ ] TLS certs generated for internal services (`keys/tls/`)

### Secrets

- [ ] `SESSION_SECRET_KEY` is cryptographically random (not the default)
- [ ] `POSTGRES_PASSWORD` and `REDIS_PASSWORD` are strong, randomly generated
- [ ] RS256 private key has restrictive permissions (`chmod 600`)
- [ ] At least one service app registered via the admin panel

### Cookie and Security Flags

- [ ] `COOKIE_SECURE=true`
- [ ] `DEBUG=false` (disables `/docs`, `/redoc`, enables fail-closed startup)
- [ ] `ALLOWED_HOSTS` set to actual domain(s)
- [ ] `CORS_ORIGINS` lists only your frontend origin(s)
- [ ] `ADMIN_EMAILS` configured

### Workers

Duar is stateless. Run multiple uvicorn workers or container replicas behind a load balancer:

```bash
uvicorn src.main:app --host 0.0.0.0 --port 9003 --workers 4
```

All instances must share the same JWT keys, PostgreSQL, and Redis.

### Backup

- PostgreSQL: use `pg_dump` or continuous archiving (WAL-G, pgBackRest)
- Redis: enable RDB snapshots or AOF persistence
- JWT keys: back up `keys/private.pem` -- losing it invalidates all issued tokens

---

## Structured Logging

Duar emits one JSON object per line to stdout. The Docker swarm log driver
collects it automatically — no sidecar or file-based shipping required.

Set the following in your production environment (or `.env.prod`):

```bash
LOG_LEVEL=INFO
LOG_FORMAT=json         # one JSON object per line
LOG_PII_REDACTION=true  # always true in production
```

The log stream is additive to the existing database `ActivityLog` table. No database
migration is required. The `ActivityLog` table is unchanged and remains the
compliance system-of-record for auditable actions.

For the full log schema, event vocabulary, and anomaly-detection guidance see
[Observability → Logging](../observability/logging.md).

---

## Health Check

The service exposes `GET /health` which returns `{"status": "ok"}`. Use this for load balancer health checks and container orchestration.

### OpenAPI

`/docs`, `/redoc`, and `/openapi.json` are only available when `DEBUG=true`. They are disabled in production.
