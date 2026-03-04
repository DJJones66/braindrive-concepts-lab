#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required" >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required" >&2
  exit 1
fi

ROUTER_HOST="${BRAINDRIVE_ROUTER_HOST:-localhost}"
ROUTER_PORT="${BRAINDRIVE_ROUTER_PORT:-9480}"
ROUTER_BASE="${BRAINDRIVE_ROUTER_BASE:-http://${ROUTER_HOST}:${ROUTER_PORT}}"
EXTRACTION_TYPE="${SCRAPE_EXTRACTION_TYPE:-text}"
DELAY_SEC="${1:-10}"

echo "Selecting random Wikipedia page..."
RANDOM_URL="$(curl -sS -L -o /dev/null -w '%{url_effective}' "https://en.wikipedia.org/wiki/Special:Random")"
echo "Testing URL: ${RANDOM_URL}"
echo "Router: ${ROUTER_BASE}"
echo "Extraction type: ${EXTRACTION_TYPE}"

REQUEST_BODY="$(
  jq -n \
    --arg message_id "manual-scrape-$(date +%s)" \
    --arg url "${RANDOM_URL}" \
    --arg extraction_type "${EXTRACTION_TYPE}" \
    '{
      protocol_version: "0.1",
      message_id: $message_id,
      intent: "web.scrape.get",
      payload: {
        url: $url,
        extraction_type: $extraction_type
      }
    }'
)"

echo "Sending one scrape request..."
curl -sS "${ROUTER_BASE}/route" \
  -H "Content-Type: application/json" \
  -d "${REQUEST_BODY}" | jq

echo "Cooldown: sleeping ${DELAY_SEC}s"
sleep "${DELAY_SEC}"
echo "Done."
