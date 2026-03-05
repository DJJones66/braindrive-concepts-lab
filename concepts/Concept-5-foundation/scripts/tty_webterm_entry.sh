#!/usr/bin/env bash
set -euo pipefail

AUTH_ENABLED="${TTY_WEBTERM_AUTH_ENABLED:-false}"
AUTH_USER="${TTY_WEBTERM_AUTH_USER:-dev}"
AUTH_PASSWORD="${TTY_WEBTERM_AUTH_PASSWORD:-change-me-now}"
TTYD_PORT="${TTY_WEBTERM_PORT_INTERNAL:-7681}"
TTYD_LOG_LEVEL="${TTY_WEBTERM_LOG_LEVEL:-2}"
TTYD_THEME_BACKGROUND="${TTY_WEBTERM_THEME_BACKGROUND:-#000000}"
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

SESSION_CMD=$'cd /workspace\npython -u scripts/cli.py || true\necho\necho "[tty-webterm] BrainDrive CLI exited. Raw shell is now active."\necho "[tty-webterm] Run: python -u scripts/cli.py  (re-enter BrainDrive NL interface)"\nexec /bin/bash -li'

TTYD_ARGS=(
  -d "${TTYD_LOG_LEVEL}"
  -p "${TTYD_PORT}"
  -W
  -t "fontSize=16"
  -t "fontFamily=monospace"
  -t "cursorStyle=block"
  -t "theme={\"background\":\"${TTYD_THEME_BACKGROUND}\"}"
)

if [ "${AUTH_ENABLED}" = "true" ] || [ "${AUTH_ENABLED}" = "1" ]; then
  if [ -z "${AUTH_USER}" ] || [ -z "${AUTH_PASSWORD}" ]; then
    echo "[tty-webterm] TTY_WEBTERM_AUTH_USER and TTY_WEBTERM_AUTH_PASSWORD are required when auth is enabled."
    exit 1
  fi
  if [ "${AUTH_PASSWORD}" = "change-me-now" ]; then
    if is_loopback_bind "${NETWORK_BIND_ADDR}"; then
      echo "[tty-webterm] WARNING: TTY_WEBTERM_AUTH_PASSWORD is using the default value."
      echo "[tty-webterm] Set a custom password in .env before exposing this service."
    else
      echo "[tty-webterm] ERROR: TTY_WEBTERM_AUTH_PASSWORD cannot remain default when NETWORK_BIND_ADDR=${NETWORK_BIND_ADDR}."
      exit 1
    fi
  fi
  TTYD_ARGS+=(-c "${AUTH_USER}:${AUTH_PASSWORD}")
fi

exec /usr/local/bin/ttyd "${TTYD_ARGS[@]}" /bin/bash -lc "${SESSION_CMD}"
