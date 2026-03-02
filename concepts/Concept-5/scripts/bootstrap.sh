#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "[error] docker is required but not found in PATH" >&2
  exit 1
fi

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "[info] created .env from .env.example"
fi

mkdir -p data/runtime data/library

HOST_UID_VALUE="$(id -u)"
HOST_GID_VALUE="$(id -g)"
echo "[info] starting services as HOST_UID=${HOST_UID_VALUE} HOST_GID=${HOST_GID_VALUE}"

HOST_UID="${HOST_UID_VALUE}" HOST_GID="${HOST_GID_VALUE}" docker compose up -d --build "$@"

wait_for_health() {
  local label="$1"
  local url="$2"
  local attempts=40
  local i
  for i in $(seq 1 "${attempts}"); do
    if curl -fsS --max-time 2 "${url}" >/dev/null 2>&1; then
      echo "[ok] ${label} healthy"
      return 0
    fi
    sleep 0.5
  done
  echo "[error] timed out waiting for ${label} health endpoint: ${url}" >&2
  return 1
}

wait_for_health "router.core" "http://localhost:${BRAINDRIVE_ROUTER_PORT:-9480}/health"
wait_for_health "intent.router.natural-language" "http://localhost:${BRAINDRIVE_INTENT_PORT:-9481}/health"

echo "[done] startup complete"
echo "Run the CLI with: python scripts/cli.py"
