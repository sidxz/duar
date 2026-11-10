#!/usr/bin/env bash
# System test: boot the production compose stack from an empty database and
# drive it from outside, the way a browser, a backend and the SDKs would.
#
#   system-tests/run.sh            build, boot, test, tear down
#   KEEP=1 system-tests/run.sh     leave the stack up afterwards (see the URLs printed)
#   system-tests/run.sh down       tear down a stack left up by KEEP=1
#
# Needs docker (compose v2), uv (with `uv sync --all-packages --extra dev` done),
# node and pnpm. Everything it generates lives in system-tests/.work; nothing
# touches ./keys, .env.prod or the dev containers. It does rebuild sdks/*/dist.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
WORK="$ROOT/system-tests/.work"
compose() {
  docker compose -p duar-system-test --env-file "$WORK/stack.env" \
    -f "$ROOT/docker-compose.prod.yml" -f "$ROOT/docker-compose.system-test.yml" "$@"
}

if [ "${1:-}" = "down" ]; then
  compose down -v --remove-orphans
  exit 0
fi

# A stack left up by an earlier run holds that run's keys and passwords.
if [ -f "$WORK/stack.env" ]; then compose down -v --remove-orphans >/dev/null 2>&1 || true; fi

# ── Keys, certs and env: what `make setup` generates for a production install ──
rm -rf "$WORK"
mkdir -p "$WORK/tls"
openssl genrsa -out "$WORK/private.pem" 2048 2>/dev/null
openssl rsa -in "$WORK/private.pem" -pubout -out "$WORK/public.pem" 2>/dev/null
"$ROOT/scripts/gen-tls-certs.sh" "$WORK/tls"
# The key the tests sign AuthZ-mode IdP tokens with; the service gets the public half.
openssl genrsa -out "$WORK/idp-private.pem" 2048 2>/dev/null
openssl rsa -in "$WORK/idp-private.pem" -pubout -out "$WORK/idp-public.pem" 2>/dev/null
# Swarm mounts secrets world-readable (0444); compose bind-mounts keep the file
# mode, and the redis and duar containers do not run as the file's owner.
chmod 644 "$WORK"/*.pem "$WORK"/tls/*

cat > "$WORK/stack.env" <<EOF
POSTGRES_DB=duar
POSTGRES_USER=duar
POSTGRES_PASSWORD=$(openssl rand -hex 24)
REDIS_PASSWORD=$(openssl rand -hex 24)
BASE_URL=https://localhost:19443
ADMIN_URL=https://localhost:19444
CORS_ORIGINS=https://localhost:19444
SESSION_SECRET_KEY=$(openssl rand -hex 32)
ADMIN_EMAILS=admin@duar.test
RATE_LIMIT_AGGREGATE=3000/minute
DUAR_PORT=127.0.0.1:19003
ADMIN_PORT=127.0.0.1:19004
DEX_CLIENT_SECRET=$(openssl rand -hex 24)
TEST_TRUSTED_ISSUER_PUBKEY=$(base64 < "$WORK/idp-public.pem" | tr -d '\n')
SYSTEM_TEST_DIR=$WORK
EOF

# ── JS SDKs: built, packed as they are published, installed with pnpm ─────────
(
  cd "$ROOT/sdks"
  if [ -n "${CI:-}" ] || [ ! -d node_modules ]; then npm ci --no-audit --no-fund; fi
  npm run build --silent
  mkdir -p "$WORK/packs"
  for pkg in js react nextjs; do
    (cd "$pkg" && npm pack --silent --pack-destination "$WORK/packs" >/dev/null)
    mv "$WORK/packs"/duar-auth-"$pkg"-*.tgz "$WORK/packs/duar-auth-$pkg.tgz"
  done
  cd "$ROOT/system-tests/js"
  rm -rf node_modules pnpm-lock.yaml
  # hoist=false: a package can load only what it declares, so an SDK that
  # forgets a dependency fails here instead of in someone's app.
  pnpm install --no-frozen-lockfile --config.hoist=false --silent
)

# ── Boot ──────────────────────────────────────────────────────────────────────
finish() {
  status=$?
  # Always keep the logs: on a red run they are the first thing to read.
  compose logs --no-color --timestamps > "$WORK/stack.log" 2>&1 || true
  if [ "${KEEP:-}" = "1" ]; then
    echo "Stack left up: https://localhost:19443 (service), https://localhost:19444 (admin)."
    echo "Tear down with: system-tests/run.sh down"
  else
    compose down -v --remove-orphans >/dev/null 2>&1 || true
  fi
  [ $status -eq 0 ] || echo "FAILED. Stack log: $WORK/stack.log" >&2
  exit $status
}
trap finish EXIT

compose build
if ! compose up -d --wait --wait-timeout 180; then
  compose ps -a
  compose logs --no-color --tail 80
  exit 1
fi

# ── Test ──────────────────────────────────────────────────────────────────────
export SYSTEM_TEST_DIR="$WORK"
# The stack's CA, for every HTTP client the tests and the SDKs create.
export SSL_CERT_FILE="$WORK/tls/ca.crt" NODE_EXTRA_CA_CERTS="$WORK/tls/ca.crt"

cd "$ROOT"
# Service flows and the Python SDK. This run also builds the world (workspace,
# apps, members) that the JS tests start from.
uv run --no-sync pytest system-tests -q -p no:cacheprovider "$@"

# The JS SDKs.
(cd "$ROOT/system-tests/js" && node --test)

# Finally: did the service log a server error while all of that ran?
compose logs --no-color duar duar-internal > "$WORK/service.log" 2>&1
uv run --no-sync python system-tests/check_logs.py "$WORK/service.log"
