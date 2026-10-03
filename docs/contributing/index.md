# Contributing

## Dev Setup

Prerequisites: Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js 18+, Docker.

```bash
git clone <repo-url> && cd identity-service
make setup    # Generate keys, TLS certs, .env files, install deps, start DB containers
make start    # Start Duar on :9003 (auto-migrates)
make admin    # Start admin panel on :9004 (optional)
```

After setup, add OAuth credentials and admin emails to `service/.env`:

```bash
vim service/.env   # GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, ADMIN_EMAILS, etc.
```

Seed test data (optional):

```bash
make seed
```

---

## Project Structure

```
identity-service/
├── service/              # FastAPI microservice
│   ├── src/
│   │   ├── main.py       # App entry point, middleware stack, lifespan
│   │   ├── config.py     # Pydantic settings (env vars)
│   │   ├── api/          # Route handlers (auth, admin, permissions, roles, etc.)
│   │   ├── models/       # SQLAlchemy 2.0 models
│   │   ├── services/     # Business logic layer
│   │   ├── middleware/    # Security headers, CORS, rate limiting
│   │   ├── auth/         # JWT signing, JWKS, OAuth providers
│   │   └── schemas/      # Pydantic request/response schemas
│   ├── migrations/       # Alembic migration scripts
│   └── tests/            # Service tests
├── sdk/                  # Python SDK (duar-auth)
│   ├── src/duar_auth/
│   └── tests/
├── sdks/                 # JS/TS SDKs
│   ├── js/               # @duar-auth/js
│   ├── react/            # @duar-auth/react
│   └── nextjs/           # @duar-auth/nextjs
├── admin/                # React admin panel (Vite + Tailwind)
├── docs/                 # MkDocs Material documentation
├── keys/                 # JWT keys + TLS certs (gitignored)
├── docker-compose.yml    # Dev containers (Postgres + Redis)
└── Makefile              # All common commands
```

The project is a [uv workspace](https://docs.astral.sh/uv/concepts/workspaces/) with `members = ["service", "sdk"]`.

---

## Code Style

Linter and formatter: [ruff](https://docs.astral.sh/ruff/) (configured in `service/pyproject.toml`).

```bash
make lint     # Check for issues
make fmt      # Auto-fix lint and formatting
```

Run `make fmt` before committing.

Key conventions:

- SQLAlchemy 2.0 `mapped_column` declarative style
- Async everywhere (SQLAlchemy async, httpx async, FastAPI async handlers)
- RBAC action names: `^[a-z][a-z0-9_.:-]*$`, namespaced by `service_name`
- All primary keys are UUID v4

---

## Running Tests

```bash
cd service && uv run pytest          # Service tests
cd sdk && uv run pytest              # SDK tests
cd service && uv run pytest -x       # Stop on first failure
```

See [Testing](testing.md) for details on test structure and conventions.

---

## Useful Commands

| Command | Description |
|---------|-------------|
| `make setup` | One-time setup (keys, certs, deps, containers) |
| `make start` | Start service on :9003 |
| `make admin` | Start admin UI on :9004 |
| `make seed` | Seed test data |
| `make lint` | Run ruff linter + format check |
| `make fmt` | Auto-fix lint and formatting |
| `make docs-serve` | MkDocs live reload |
| `make clean` | Stop containers, wipe DB |
| `make nuke` | Full reset (containers + keys + deps + env files) |
| `make release VERSION=x.y.z` | Release all packages |
