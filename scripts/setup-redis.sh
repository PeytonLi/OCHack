#!/usr/bin/env bash
# Download and build Redis locally inside the repo.
# Usage: bash scripts/setup-redis.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REDIS_ROOT="$ROOT_DIR/.redis"
SRC_ROOT="$REDIS_ROOT/src"
BUILD_ROOT="$SRC_ROOT/redis-stable"
ARCHIVE="$SRC_ROOT/redis-stable.tar.gz"
URL="${REDIS_DOWNLOAD_URL:-https://download.redis.io/redis-stable.tar.gz}"
JOBS="${REDIS_BUILD_JOBS:-2}"

mkdir -p "$SRC_ROOT" "$REDIS_ROOT/bin"

echo "Downloading Redis from $URL..."
curl -L "$URL" -o "$ARCHIVE"

rm -rf "$BUILD_ROOT"
tar -xzf "$ARCHIVE" -C "$SRC_ROOT"

echo "Building Redis..."
make -C "$BUILD_ROOT" -j"$JOBS"

cp "$BUILD_ROOT/src/redis-server" "$REDIS_ROOT/bin/redis-server"
cp "$BUILD_ROOT/src/redis-cli" "$REDIS_ROOT/bin/redis-cli"

echo "Redis installed in $REDIS_ROOT/bin"
echo "Start it with: bash scripts/start-redis.sh start"
