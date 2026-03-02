#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${STACK_DIR}"

if [ "${1:-}" = "--no-up" ]; then
  python3 scripts/demo.py
  exit 0
fi

echo "Starting Concept-1 stack..."
docker compose up -d

echo "Running demo scenarios..."
python3 scripts/demo.py

echo "Demo complete. Stack is still running."
echo "Stop with: docker compose down"
