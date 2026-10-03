#!/usr/bin/env bash
set -euo pipefail

# Release script — bumps all packages to the same version, commits, tags, and pushes.
#
# Usage:
#   ./scripts/release.sh 0.6.0
#   make release VERSION=0.6.0

VERSION="${1:?Usage: $0 <version>  (e.g. 0.6.0)}"

# Validate semver-ish format
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Error: version must be semver (e.g. 0.6.0), got '$VERSION'" >&2
  exit 1
fi

# Must be on main
BRANCH=$(git branch --show-current)
if [[ "$BRANCH" != "main" ]]; then
  echo "Error: must be on 'main' branch, currently on '$BRANCH'" >&2
  exit 1
fi

# Must be clean
if [[ -n $(git status --porcelain) ]]; then
  echo "Error: working tree is not clean — commit or stash changes first" >&2
  exit 1
fi

# Check tags don't already exist
for TAG in "js-sdk-v${VERSION}" "sdk-v${VERSION}" "service-v${VERSION}" "admin-v${VERSION}"; do
  if git rev-parse "$TAG" >/dev/null 2>&1; then
    echo "Error: tag '$TAG' already exists" >&2
    exit 1
  fi
done

echo "Releasing v${VERSION}..."
echo ""

# Pull latest
git pull origin main

# ── Bump versions ──────────────────────────────────────────────────

# Python SDK
sed -i '' "s/^version = \".*\"/version = \"${VERSION}\"/" sdk/pyproject.toml

# Service
sed -i '' "s/^version = \".*\"/version = \"${VERSION}\"/" service/pyproject.toml

# JS SDK packages
for PKG in sdks/js/package.json sdks/react/package.json sdks/nextjs/package.json; do
  sed -i '' "s/\"version\": \".*\"/\"version\": \"${VERSION}\"/" "$PKG"
done

# Admin panel
sed -i '' "s/\"version\": \".*\"/\"version\": \"${VERSION}\"/" admin/package.json

# Update peer dependency ranges for @duar-auth/* packages
sed -i '' "s/\"@duar-auth\/js\": \"\\^.*\"/\"@duar-auth\/js\": \"^${VERSION}\"/" sdks/react/package.json
sed -i '' "s/\"@duar-auth\/js\": \"\\^.*\"/\"@duar-auth\/js\": \"^${VERSION}\"/" sdks/nextjs/package.json
sed -i '' "s/\"@duar-auth\/react\": \"\\^.*\"/\"@duar-auth\/react\": \"^${VERSION}\"/" sdks/nextjs/package.json

# Refresh the uv workspace lockfile so it tracks the bumped member versions.
echo "Updating uv.lock..."
uv lock

# ── Verify builds ─────────────────────────────────────────────────

echo "Building JS SDKs..."
(cd sdks && npm run build --silent)

echo "Running JS tests..."
(cd sdks && npm test --silent)

echo "Running Python SDK tests..."
(cd sdk && uv sync --extra dev -q && uv run pytest -q)

echo ""

# ── Commit, tag, push ─────────────────────────────────────────────

git add \
  sdk/pyproject.toml \
  service/pyproject.toml \
  sdks/js/package.json \
  sdks/react/package.json \
  sdks/nextjs/package.json \
  admin/package.json \
  uv.lock

git commit -m "chore: bump all packages to ${VERSION}"

git tag "js-sdk-v${VERSION}"
git tag "sdk-v${VERSION}"
git tag "service-v${VERSION}"
git tag "admin-v${VERSION}"

git push origin main
# Push tags individually — GitHub Actions drops events when multiple tags are pushed at once
git push origin "js-sdk-v${VERSION}"
git push origin "sdk-v${VERSION}"
git push origin "service-v${VERSION}"
git push origin "admin-v${VERSION}"

echo ""
echo "Released v${VERSION}:"
echo "  js-sdk-v${VERSION}  → Publish JS SDK (npm)"
echo "  sdk-v${VERSION}     → Publish Python SDK (PyPI)"
echo "  service-v${VERSION} → Publish Docker image (GHCR)"
echo "  admin-v${VERSION}   → Publish Admin Docker image (GHCR)"
echo ""
echo "Check workflows: gh run list --limit 5"
