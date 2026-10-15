FROM python:3.14-alpine

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

# The app runs from .venv (+ the static uv binary); the base image's global
# pip/setuptools are never used at runtime and only add CVEs (pip vendors
# msgpack and urllib3). Drop them so the shipped image scans clean.
RUN SP="$(python3 -c 'import site; print(site.getsitepackages()[0])')" && \
    rm -rf "$SP"/pip "$SP"/pip-* "$SP"/setuptools "$SP"/setuptools-* \
           "$SP"/pkg_resources "$SP"/_distutils_hack && \
    rm -f /usr/local/bin/pip /usr/local/bin/pip3

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
