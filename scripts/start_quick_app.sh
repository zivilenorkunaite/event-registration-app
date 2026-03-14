#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required. Install Node.js first."
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required."
  exit 1
fi

if [[ -x "$ROOT_DIR/.venv/bin/uvicorn" ]]; then
  UVICORN_BIN="$ROOT_DIR/.venv/bin/uvicorn"
elif command -v uvicorn >/dev/null 2>&1; then
  UVICORN_BIN="uvicorn"
else
  echo "uvicorn not found. Install Python dependencies first."
  exit 1
fi

cleanup() {
  if [[ "${STARTED_BACKEND:-0}" == "1" && -n "${BACKEND_PID:-}" ]]; then
    kill "$BACKEND_PID" >/dev/null 2>&1 || true
  fi
  if [[ "${STARTED_FRONTEND:-0}" == "1" && -n "${FRONTEND_PID:-}" ]]; then
    kill "$FRONTEND_PID" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT INT TERM

STARTED_BACKEND=0
STARTED_FRONTEND=0

echo "Preparing frontend dependencies..."
cd "$ROOT_DIR/app/frontend"
npm install >/dev/null

echo "Starting backend (http://localhost:8000)..."
if curl -sS "http://localhost:8000/health" >/dev/null 2>&1; then
  echo "Backend already running on :8000"
else
  cd "$ROOT_DIR"
  "$UVICORN_BIN" backend.main:app --host 0.0.0.0 --port 8000 --app-dir app >/tmp/quick-backend.log 2>&1 &
  BACKEND_PID=$!
  STARTED_BACKEND=1
fi

for _ in {1..30}; do
  if curl -sS "http://localhost:8000/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "Starting frontend (http://localhost:8080)..."
if curl -sS "http://localhost:8080" >/dev/null 2>&1; then
  echo "Frontend already running on :8080"
else
  cd "$ROOT_DIR/app/frontend"
  npm run dev -- --host 0.0.0.0 --port 8080 >/tmp/quick-frontend.log 2>&1 &
  FRONTEND_PID=$!
  STARTED_FRONTEND=1
fi

for _ in {1..30}; do
  if curl -sS "http://localhost:8080" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo ""
echo "Quick start ready:"
echo "- Backend:  http://localhost:8000"
echo "- Frontend: http://localhost:8080"
echo "Press Ctrl+C to stop services started by this script."

if [[ "$STARTED_BACKEND" == "1" ]]; then
  wait "$BACKEND_PID"
fi
if [[ "$STARTED_FRONTEND" == "1" ]]; then
  wait "$FRONTEND_PID"
fi
