#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-$SCRIPT_DIR}"
BRANCH="${BRANCH:-main}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
PORTS_TO_FREE="${PORTS_TO_FREE:-8000}"
REQUIRED_NETWORK="${REQUIRED_NETWORK:-hope_default}"

cd "$APP_DIR"

echo "[deploy] app dir: $APP_DIR"
echo "[deploy] branch: $BRANCH"
echo "[deploy] compose file: $COMPOSE_FILE"
echo "[deploy] ports to free: $PORTS_TO_FREE"
echo "[deploy] required network: $REQUIRED_NETWORK"

free_port() {
  local port="$1"
  [ -n "$port" ] || return 0

  echo "[deploy] checking port $port"

  if command -v docker >/dev/null 2>&1; then
    local container_ids
    container_ids=$(docker ps --filter "publish=$port" -q || true)
    if [ -n "$container_ids" ]; then
      echo "[deploy] stopping docker containers exposing port $port"
      docker stop $container_ids || true
    fi
  fi

  if command -v fuser >/dev/null 2>&1; then
    if fuser "${port}/tcp" >/dev/null 2>&1; then
      echo "[deploy] killing remaining processes on port $port via fuser"
      fuser -k "${port}/tcp" || true
    fi
    return 0
  fi

  if command -v lsof >/dev/null 2>&1; then
    local pids
    pids=$(lsof -ti tcp:"$port" || true)
    if [ -n "$pids" ]; then
      echo "[deploy] killing remaining processes on port $port via lsof"
      kill -9 $pids || true
    fi
    return 0
  fi

  if command -v ss >/dev/null 2>&1; then
    local pids
    pids=$(ss -ltnp "sport = :$port" 2>/dev/null | grep -o 'pid=[0-9]\+' | cut -d= -f2 | sort -u || true)
    if [ -n "$pids" ]; then
      echo "[deploy] killing remaining processes on port $port via ss"
      kill -9 $pids || true
    fi
  fi
}

ensure_network() {
  if [ -z "$REQUIRED_NETWORK" ]; then
    return 0
  fi

  if docker network inspect "$REQUIRED_NETWORK" >/dev/null 2>&1; then
    echo "[deploy] docker network $REQUIRED_NETWORK already exists"
    return 0
  fi

  echo "[deploy] creating docker network $REQUIRED_NETWORK"
  docker network create "$REQUIRED_NETWORK" >/dev/null
}

git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"

ensure_network

docker compose -f "$COMPOSE_FILE" down --remove-orphans || true

for port in $PORTS_TO_FREE; do
  free_port "$port"
done

docker compose -f "$COMPOSE_FILE" up -d --build --remove-orphans

docker image prune -f

echo "[deploy] done"
