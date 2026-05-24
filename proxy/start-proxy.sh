#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${1:-${PROJECT_ROOT}/proxy/.env}"

if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
fi

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "Missing required env var: OPENAI_API_KEY" >&2
  exit 1
fi

if [[ -z "${INTERNAL_JWT_SECRET:-}" ]]; then
  echo "Missing required env var: INTERNAL_JWT_SECRET" >&2
  exit 1
fi

HOST="${PROXY_HOST:-127.0.0.1}"
PORT="${PROXY_PORT:-18980}"
PYTHON_BIN="${PROXY_PYTHON_BIN:-python3}"
UVICORN_ARGS="${UVICORN_EXTRA_ARGS:-}"

cd "${PROJECT_ROOT}"

echo "Start proxy at http://${HOST}:${PORT}"
echo "Config: OPENAI_BASE_URL=${OPENAI_BASE_URL:-https://api.openai.com/v1}"

exec "${PYTHON_BIN}" -m uvicorn proxy.app.main:app --host "${HOST}" --port "${PORT}" ${UVICORN_ARGS}
