#!/usr/bin/env bash
set -euo pipefail

AUTH_USER="${DEV_WEBTERM_AUTH_USER:-dev}"
AUTH_PASSWORD="${DEV_WEBTERM_AUTH_PASSWORD:-change-me-now}"
TTYD_PORT="${DEV_WEBTERM_PORT_INTERNAL:-7681}"
TTYD_LOG_LEVEL="${DEV_WEBTERM_LOG_LEVEL:-2}"
TTYD_THEME_BACKGROUND="${DEV_WEBTERM_THEME_BACKGROUND:-#000000}"
NETWORK_BIND_ADDR="${NETWORK_BIND_ADDR:-127.0.0.1}"

is_loopback_bind() {
  local bind="$1"
  case "${bind}" in
    127.0.0.1|localhost|::1|\[::1\])
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

if [ -z "${AUTH_USER}" ] || [ -z "${AUTH_PASSWORD}" ]; then
  echo "[webterm] DEV_WEBTERM_AUTH_USER and DEV_WEBTERM_AUTH_PASSWORD are required."
  exit 1
fi

if [ "${AUTH_PASSWORD}" = "change-me-now" ]; then
  if is_loopback_bind "${NETWORK_BIND_ADDR}"; then
    echo "[webterm] WARNING: DEV_WEBTERM_AUTH_PASSWORD is using the default value."
    echo "[webterm] Set a custom password in .env before exposing this service."
  else
    echo "[webterm] ERROR: DEV_WEBTERM_AUTH_PASSWORD cannot remain default when NETWORK_BIND_ADDR=${NETWORK_BIND_ADDR}."
    exit 1
  fi
fi

SESSION_CMD=$'cd /workspace\npython -u scripts/cli.py || true\necho\necho "[webterm] BrainDrive CLI exited. Raw shell is now active."\necho "[webterm] Run: python -u scripts/cli.py  (re-enter BrainDrive NL interface)"\nexec /bin/bash -li'

exec /usr/local/bin/ttyd \
  -d "${TTYD_LOG_LEVEL}" \
  -p "${TTYD_PORT}" \
  -W \
  -t "theme={\"background\":\"${TTYD_THEME_BACKGROUND}\"}" \
  -c "${AUTH_USER}:${AUTH_PASSWORD}" \
  /bin/bash -lc "${SESSION_CMD}"
