FROM python:3.12-alpine

RUN apk upgrade --no-cache

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install dependencies using standalone (non-workspace) resolution
COPY pyproject.toml ./
RUN uv sync --no-dev --no-install-project

COPY src/ src/
COPY migrations/ migrations/
COPY alembic.ini .

RUN adduser -D -u 1000 duar
USER duar

EXPOSE 9003

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -qO- http://localhost:9003/health || exit 1

CMD [".venv/bin/uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "9003", "--no-server-header"]
