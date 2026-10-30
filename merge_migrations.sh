#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-$SCRIPT_DIR}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"

cd "$APP_DIR"

echo "[merge] app dir: $APP_DIR"
echo "[merge] compose file: $COMPOSE_FILE"
echo "[merge] generating merge migration"

docker compose -f "$COMPOSE_FILE" run --rm --no-deps api python manage.py makemigrations --merge

echo "[merge] showing migration plan"
docker compose -f "$COMPOSE_FILE" run --rm --no-deps api python manage.py migrate --plan

echo "[merge] done"
echo "[merge] IMPORTANT: commit the generated migration file to Git before deploying."
