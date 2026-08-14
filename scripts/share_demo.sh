#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8000}"
LOG_FILE="${TMPDIR:-/tmp}/mechabellum-cloudflared-${PORT}.log"
BACKEND_PID=""
TUNNEL_PID=""
LOG_PID=""

cleanup() {
  trap - EXIT INT TERM
  [[ -n "${LOG_PID}" ]] && kill "${LOG_PID}" 2>/dev/null || true
  [[ -n "${TUNNEL_PID}" ]] && kill "${TUNNEL_PID}" 2>/dev/null || true
  [[ -n "${BACKEND_PID}" ]] && kill "${BACKEND_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared is required. Install it from https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/" >&2
  exit 1
fi

echo "Building frontend..."
npm --prefix "${ROOT_DIR}/frontend" run build

echo "Starting same-origin FastAPI server on http://127.0.0.1:${PORT}..."
(
  cd "${ROOT_DIR}"
  exec python3 -m uvicorn backend.run:app --host 127.0.0.1 --port "${PORT}"
) >"${TMPDIR:-/tmp}/mechabellum-backend-${PORT}.log" 2>&1 &
BACKEND_PID=$!

for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done
if ! curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
  echo "Backend did not become healthy; see ${TMPDIR:-/tmp}/mechabellum-backend-${PORT}.log" >&2
  exit 1
fi

: >"${LOG_FILE}"
echo "Starting Cloudflare Quick Tunnel..."
cloudflared tunnel --url "http://127.0.0.1:${PORT}" >"${LOG_FILE}" 2>&1 &
TUNNEL_PID=$!

PUBLIC_URL=""
for _ in $(seq 1 60); do
  PUBLIC_URL="$(grep -Eo 'https://[-a-z0-9]+\.trycloudflare\.com' "${LOG_FILE}" | head -n 1 || true)"
  [[ -n "${PUBLIC_URL}" ]] && break
  sleep 1
done
if [[ -z "${PUBLIC_URL}" ]]; then
  echo "Tunnel did not provide a public URL; see ${LOG_FILE}" >&2
  exit 1
fi

echo
echo "Demo is live: ${PUBLIC_URL}"
echo "Keep this terminal open. Press Ctrl+C to stop the backend and tunnel."
echo
tail -f "${LOG_FILE}" &
LOG_PID=$!
wait "${TUNNEL_PID}"
