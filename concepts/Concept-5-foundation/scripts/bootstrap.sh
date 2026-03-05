#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

LEGACY_DEFAULT_REGISTRATION_TOKEN="braindrive-mvp-dev-token"

if ! command -v docker >/dev/null 2>&1; then
  echo "[error] docker is required but not found in PATH" >&2
  exit 1
fi

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "[info] created .env from .env.example"
fi

read_env_value() {
  local key="$1"
  if [ -f ".env" ]; then
    grep -E "^${key}=" .env | tail -n 1 | cut -d '=' -f 2- || true
  fi
}

normalize_flag_value() {
  local raw="$1"
  printf '%s' "${raw}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]' | tr -d '"' | tr -d "'"
}

normalize_env_value() {
  local raw="$1"
  raw="$(printf '%s' "${raw}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  raw="${raw%\"}"
  raw="${raw#\"}"
  raw="${raw%\'}"
  raw="${raw#\'}"
  printf '%s' "${raw}"
}

upsert_env_value() {
  local key="$1"
  local value="$2"
  local tmp
  tmp="$(mktemp)"
  awk -v key="${key}" -v value="${value}" '
    BEGIN { written = 0 }
    $0 ~ ("^" key "=") {
      if (written == 0) {
        print key "=" value
        written = 1
      }
      next
    }
    { print }
    END {
      if (written == 0) {
        print key "=" value
      }
    }
  ' .env > "${tmp}"
  mv "${tmp}" .env
}

resolve_boolean_env() {
  local key="$1"
  local default_value="$2"
  local raw="${!key:-}"
  if [ -z "${raw}" ]; then
    raw="$(read_env_value "${key}")"
  fi
  raw="$(normalize_flag_value "${raw}")"
  case "${raw}" in
    1|true|yes|on)
      echo "true"
      ;;
    0|false|no|off)
      echo "false"
      ;;
    "")
      echo "${default_value}"
      ;;
    *)
      echo "[warn] invalid ${key}='${raw}', defaulting to ${default_value}" >&2
      echo "${default_value}"
      ;;
  esac
}

generate_registration_token() {
  local random_hex=""
  if command -v openssl >/dev/null 2>&1; then
    random_hex="$(openssl rand -hex 24)"
  elif [ -r "/dev/urandom" ] && command -v od >/dev/null 2>&1; then
    random_hex="$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
  fi

  if [ -z "${random_hex}" ]; then
    echo "[error] failed to generate ROUTER_REGISTRATION_TOKEN" >&2
    exit 1
  fi

  printf 'bdrt-%s' "${random_hex}"
}

resolve_registration_token() {
  local raw="${ROUTER_REGISTRATION_TOKEN:-}"
  if [ -z "${raw}" ]; then
    raw="$(read_env_value "ROUTER_REGISTRATION_TOKEN")"
  fi
  raw="$(normalize_env_value "${raw}")"

  if [ -n "${raw}" ] && [ "${raw}" != "${LEGACY_DEFAULT_REGISTRATION_TOKEN}" ]; then
    echo "${raw}"
    return 0
  fi

  if [ "${BRAINDRIVE_DEV_MODE_VALUE}" != "true" ]; then
    echo "[error] ROUTER_REGISTRATION_TOKEN must be explicitly set and non-default when BRAINDRIVE_DEV_MODE=false." >&2
    exit 1
  fi

  local generated
  generated="$(generate_registration_token)"
  upsert_env_value "ROUTER_REGISTRATION_TOKEN" "${generated}"
  echo "[info] generated ROUTER_REGISTRATION_TOKEN and saved to .env" >&2
  echo "${generated}"
}

BRAINDRIVE_DEV_MODE_VALUE="$(resolve_boolean_env "BRAINDRIVE_DEV_MODE" "true")"
NETWORK_EXPOSED_VALUE="$(resolve_boolean_env "NETWORK_EXPOSED" "false")"
ROUTER_REGISTRATION_TOKEN_VALUE="$(resolve_registration_token)"
NETWORK_BIND_ADDR_VALUE="127.0.0.1"
if [ "${NETWORK_EXPOSED_VALUE}" = "true" ]; then
  NETWORK_BIND_ADDR_VALUE="0.0.0.0"
fi

mkdir -p data/runtime data/runtime/dev-webterm data/library

HOST_UID_VALUE="$(id -u)"
HOST_GID_VALUE="$(id -g)"
echo "[info] starting services as HOST_UID=${HOST_UID_VALUE} HOST_GID=${HOST_GID_VALUE}"
echo "[info] runtime mode BRAINDRIVE_DEV_MODE=${BRAINDRIVE_DEV_MODE_VALUE}"
echo "[info] network exposure mode NETWORK_EXPOSED=${NETWORK_EXPOSED_VALUE} -> NETWORK_BIND_ADDR=${NETWORK_BIND_ADDR_VALUE}"
echo "[info] registration token configured (length=${#ROUTER_REGISTRATION_TOKEN_VALUE})"

HOST_UID="${HOST_UID_VALUE}" \
HOST_GID="${HOST_GID_VALUE}" \
BRAINDRIVE_DEV_MODE="${BRAINDRIVE_DEV_MODE_VALUE}" \
NETWORK_EXPOSED="${NETWORK_EXPOSED_VALUE}" \
NETWORK_BIND_ADDR="${NETWORK_BIND_ADDR_VALUE}" \
ROUTER_REGISTRATION_TOKEN="${ROUTER_REGISTRATION_TOKEN_VALUE}" \
docker compose up -d --build "$@"

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

bootstrap_runtime() {
  local router_url="http://localhost:${BRAINDRIVE_ROUTER_PORT:-9480}/route"
  local payload='{"protocol_version":"0.1","message_id":"bootstrap-init","intent":"system.bootstrap","payload":{}}'
  if curl -fsS --max-time 10 "${router_url}" -H 'Content-Type: application/json' -d "${payload}" >/dev/null; then
    echo "[ok] runtime bootstrap completed"
  else
    echo "[warn] runtime bootstrap request failed; you can retry via CLI /health -> bootstrap flow" >&2
  fi
}

bootstrap_runtime

echo "[done] startup complete"
echo "Run the CLI with: python scripts/cli.py"
