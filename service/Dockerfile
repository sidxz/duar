FROM python:3.12-alpine

RUN apk upgrade --no-cache

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Build context is the repo root (docker build -f service/Dockerfile .) so the
# image installs exactly what the workspace uv.lock pins, the same versions the
# test suite runs against. --locked fails the build if the lock is stale.
COPY pyproject.toml uv.lock ./
COPY service/pyproject.toml service/
COPY sdk/pyproject.toml sdk/
RUN uv sync --locked --no-dev --package duar-service --no-install-workspace

COPY service/src/ src/
COPY service/migrations/ migrations/
COPY service/alembic.ini .

# Release version from the git tag (CI passes it); local builds report 0.0.0+dev.
ARG APP_VERSION=0.0.0+dev
ENV APP_VERSION=$APP_VERSION

RUN adduser -D -u 1000 duar
USER duar

EXPOSE 9003

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -qO- http://localhost:9003/health || exit 1

CMD [".venv/bin/uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "9003", "--no-server-header"]
