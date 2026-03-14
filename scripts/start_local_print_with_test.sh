#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="${NIIMBOT_CONFIG_FILE:-$ROOT_DIR/.env}"
EXAMPLE_FILE="$ROOT_DIR/.env.example"

if [[ ! -f "$CONFIG_FILE" ]]; then
  if [[ -f "$EXAMPLE_FILE" ]]; then
    cp "$EXAMPLE_FILE" "$CONFIG_FILE"
  fi

  cat <<EOF
Created config file at:
  $CONFIG_FILE

Set NIIMBOT_ADDRESS (and optionally NIIMBOT_TRANSPORT / NIIMBOT_PRINT_TASK), then rerun.
EOF
  exit 1
fi

set -a
source "$CONFIG_FILE"
set +a

NIIMBOT_SERVER_URL="${NIIMBOT_SERVER_URL:-http://localhost:5050}"
NIIMBOT_TRANSPORT="${NIIMBOT_TRANSPORT:-ble}"
NIIMBOT_PRINT_TASK="${NIIMBOT_PRINT_TASK:-B1}"

URL_NO_PROTO="${NIIMBOT_SERVER_URL#http://}"
URL_NO_PROTO="${URL_NO_PROTO#https://}"
SERVER_HOST="${URL_NO_PROTO%%:*}"
SERVER_PORT_PART="${URL_NO_PROTO#*:}"
if [[ "$SERVER_PORT_PART" == "$URL_NO_PROTO" ]]; then
  SERVER_PORT="5050"
else
  SERVER_PORT="${SERVER_PORT_PART%%/*}"
fi

run_niimblue() {
  if command -v niimblue-cli >/dev/null 2>&1; then
    niimblue-cli "$@"
  else
    npx -y @mmote/niimblue-node "$@"
  fi
}

if ! command -v npx >/dev/null 2>&1; then
  echo "npx is required. Install Node.js first."
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
  if [[ -n "${UVICORN_PID:-}" ]]; then
    kill "$UVICORN_PID" >/dev/null 2>&1 || true
  fi
  if [[ "${STARTED_NIIMBLUE:-0}" == "1" && -n "${NIIMBLUE_PID:-}" ]]; then
    kill "$NIIMBLUE_PID" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT INT TERM

echo "Starting niimblue server..."
STARTED_NIIMBLUE=0
if curl -sS "$NIIMBOT_SERVER_URL" >/dev/null 2>&1; then
  echo "niimblue server already running at $NIIMBOT_SERVER_URL"
else
  run_niimblue server -h "$SERVER_HOST" -p "$SERVER_PORT" >/tmp/niimblue-server.log 2>&1 &
  NIIMBLUE_PID=$!
  STARTED_NIIMBLUE=1
fi

if [[ -z "${NIIMBOT_ADDRESS:-}" ]]; then
  echo "NIIMBOT_ADDRESS is empty. Scanning for nearby printers..."
  SCAN_OUTPUT="$(run_niimblue scan -t "$NIIMBOT_TRANSPORT" -n 5000 2>/dev/null || true)"

  AUTO_DEVICE="$(printf "%s\n" "$SCAN_OUTPUT" \
    | sed -E 's/^[^:]*:[[:space:]]*//' \
    | sed '/^[[:space:]]*$/d' \
    | grep -Eiv '^unknown$' \
    | grep -Ei 'niim|b3|b1|b21|d110|label|print' \
    | head -n 1 || true)"

  if [[ -n "$AUTO_DEVICE" ]]; then
    NIIMBOT_ADDRESS="$AUTO_DEVICE"
    echo "Auto-selected printer identifier: $NIIMBOT_ADDRESS"
  else
    echo "Could not auto-detect printer identifier."
    echo "Scan output:"
    printf "%s\n" "$SCAN_OUTPUT"
    echo "On macOS, set NIIMBOT_ADDRESS to the Bluetooth device name from scan output."
    echo "Run: npx -y @mmote/niimblue-node scan -t ble"
    exit 1
  fi
fi

for _ in {1..30}; do
  if curl -sS "$NIIMBOT_SERVER_URL" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "Connecting printer ($NIIMBOT_TRANSPORT / $NIIMBOT_ADDRESS)..."
CONNECT_RESPONSE="$(curl -sS -X POST "$NIIMBOT_SERVER_URL/connect" -H "Content-Type: application/json" -d "{\"transport\":\"$NIIMBOT_TRANSPORT\",\"address\":\"$NIIMBOT_ADDRESS\"}")"

if [[ "$CONNECT_RESPONSE" == *"error"* && "$CONNECT_RESPONSE" != *"Already connected"* ]]; then
  echo "Printer connect response: $CONNECT_RESPONSE"
  echo "Failed to connect printer. Check .env values."
  exit 1
fi

if [[ "$CONNECT_RESPONSE" == *"Already connected"* ]]; then
  echo "Printer is already connected."
fi

echo "Printer connected."
echo "Starting FastAPI on http://localhost:8000..."

cd "$ROOT_DIR"
NIIMBOT_SERVER_URL="$NIIMBOT_SERVER_URL" \
NIIMBOT_TRANSPORT="$NIIMBOT_TRANSPORT" \
NIIMBOT_ADDRESS="$NIIMBOT_ADDRESS" \
NIIMBOT_PRINT_TASK="$NIIMBOT_PRINT_TASK" \
"$UVICORN_BIN" backend.main:app --host 0.0.0.0 --port 8000 --app-dir app &
UVICORN_PID=$!

for _ in {1..30}; do
  if curl -sS "http://localhost:8000/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "Sending TEST print..."
TEST_RESPONSE="$(curl -sS -X POST "http://localhost:8000/api/print" -H "Content-Type: application/json" -d '{"firstName":"TEST","company":"LOCAL","groupName":"Energy & Utilities Data Connect","location":"Sydney"}')"

if [[ "$TEST_RESPONSE" == *"detail"* || "$TEST_RESPONSE" == *"error"* ]]; then
  echo "TEST print response: $TEST_RESPONSE"
  echo "Test print failed."
  exit 1
fi

echo "TEST print sent successfully."
echo "Press Ctrl+C to stop both FastAPI and niimblue server."

wait "$UVICORN_PID"