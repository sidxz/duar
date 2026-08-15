# Getting Started

## Prerequisites

| Tool | Version | Why |
|------|---------|-----|
| **Docker** + **Docker Compose** | latest | Runs PostgreSQL 16, Redis 7, and the Duar service |
| **Python** | 3.12+ | FastAPI service and SDK |
| **uv** | latest | Python package/workspace manager |
| **Node.js** | 18+ | Admin panel, JS SDKs, frontend apps |
| **OpenSSL** | any | JWT key and TLS cert generation (handled by `make setup`) |

You also need **OAuth credentials** from at least one identity provider. Google is the fastest to configure for development.

## Three Steps

### 1. Run Duar

Clone the repository and run the one-time setup:

```bash
git clone <repo-url> duar && cd duar
make setup
```

`make setup` does all of this in one shot:

- Generates an **RSA key pair** for JWT signing (`keys/private.pem`, `keys/public.pem`)
- Generates **TLS certificates** for Postgres and Redis (`keys/tls/`)
- Creates `service/.env` (dev) and `.env.prod` with random secrets
- Installs Python and Node.js dependencies
- Starts PostgreSQL and Redis containers

After setup completes, add your OAuth credentials and admin email:

```bash
# Edit service/.env — set at minimum:
#   GOOGLE_CLIENT_ID=...
#   GOOGLE_CLIENT_SECRET=...
#   ADMIN_EMAILS=you@example.com
```

Start the service and admin panel:

```bash
make start    # Duar on :9003 (auto-migrates the database)
make admin    # Admin UI on :9004 (separate terminal)
```

Verify:

```bash
curl http://localhost:9003/health
```

### 2. Configure Your IdP

In the [Quickstart](quickstart.md), you will create a Google OAuth client, register your apps in the Duar admin panel, and get a service API key.

### 3. Integrate Your App

Install the SDK in your backend and frontend, add a few lines of configuration, and Duar handles authentication and authorization. The [Quickstart](quickstart.md) walks through this end to end with working code.

## Production Deployment

For Docker-based production deployment:

```bash
# make setup already created .env.prod with random passwords
# Edit it to set your real values:
vim .env.prod   # BASE_URL, ADMIN_URL, OAuth creds, ADMIN_EMAILS

docker swarm init    # once per host (overlay network + secrets need swarm mode)
docker stack deploy -c docker-compose.prod.yml duar
```

## Next

Follow the [Quickstart](quickstart.md) to configure Google OAuth, register your apps, and run a working example.
