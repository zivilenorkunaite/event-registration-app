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

Set NIIMBOT_ADDRESS and local-agent Databricks settings, then rerun.
EOF
  exit 1
fi

set -a
source "$CONFIG_FILE"
set +a

NIIMBOT_SERVER_URL="${NIIMBOT_SERVER_URL:-http://localhost:5050}"
NIIMBOT_TRANSPORT="${NIIMBOT_TRANSPORT:-ble}"
LOCAL_AGENT_LOCAL_RUN="${LOCAL_AGENT_LOCAL_RUN:-false}"
LOCAL_AGENT_UI_PORT="${LOCAL_AGENT_UI_PORT:-8501}"
LOCAL_AGENT_UI_LOG_FILE="${LOCAL_AGENT_UI_LOG_FILE:-/tmp/local-agent-ui.log}"

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

if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
else
  echo "Python not found. Install dependencies first."
  exit 1
fi

if [[ -x "$ROOT_DIR/.venv/bin/streamlit" ]]; then
  STREAMLIT_BIN="$ROOT_DIR/.venv/bin/streamlit"
elif command -v streamlit >/dev/null 2>&1; then
  STREAMLIT_BIN="streamlit"
else
  echo "streamlit not found. Install Python dependencies first."
  exit 1
fi

if [[ -z "${NIIMBOT_ADDRESS:-}" ]]; then
  echo "NIIMBOT_ADDRESS is empty. Set it in .env and rerun."
  exit 1
fi

if [[ "$LOCAL_AGENT_LOCAL_RUN" != "true" ]]; then
  if [[ -z "${DATABRICKS_WAREHOUSE_ID:-}" ]]; then
    echo "DATABRICKS_WAREHOUSE_ID is required when LOCAL_AGENT_LOCAL_RUN=false."
    exit 1
  fi
fi

cleanup() {
  if [[ -n "${AGENT_PID:-}" ]]; then
    kill "$AGENT_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "${UI_PID:-}" ]]; then
    kill "$UI_PID" >/dev/null 2>&1 || true
  fi
  if [[ "${STARTED_NIIMBLUE:-0}" == "1" && -n "${NIIMBLUE_PID:-}" ]]; then
    kill "$NIIMBLUE_PID" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT INT TERM

STARTED_NIIMBLUE=0
if curl -sS "$NIIMBOT_SERVER_URL" >/dev/null 2>&1; then
  echo "niimblue server already running at $NIIMBOT_SERVER_URL"
else
  echo "Starting niimblue server on ${SERVER_HOST}:${SERVER_PORT}..."
  run_niimblue server -h "$SERVER_HOST" -p "$SERVER_PORT" >/tmp/niimblue-server.log 2>&1 &
  NIIMBLUE_PID=$!
  STARTED_NIIMBLUE=1
fi

for _ in {1..30}; do
  if curl -sS "$NIIMBOT_SERVER_URL" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl -sS "$NIIMBOT_SERVER_URL" >/dev/null 2>&1; then
  echo "niimblue server is not reachable at $NIIMBOT_SERVER_URL"
  exit 1
fi

echo "Connecting printer ($NIIMBOT_TRANSPORT / $NIIMBOT_ADDRESS)..."
CONNECT_RESPONSE="$(curl -sS -X POST "$NIIMBOT_SERVER_URL/connect" -H "Content-Type: application/json" -d "{\"transport\":\"$NIIMBOT_TRANSPORT\",\"address\":\"$NIIMBOT_ADDRESS\"}")"

if [[ "$CONNECT_RESPONSE" == *"error"* && "$CONNECT_RESPONSE" != *"Already connected"* ]]; then
  echo "Printer connect response: $CONNECT_RESPONSE"
  echo "Failed to connect printer. Check .env values."
  exit 1
fi

echo "Printer connected."
echo "Starting local agent..."
echo "Starting local agent UI on http://localhost:${LOCAL_AGENT_UI_PORT} ..."
echo "UI log: ${LOCAL_AGENT_UI_LOG_FILE}"
echo "Press Ctrl+C to stop local agent, Streamlit UI, and niimblue (if started by this script)."

cd "$ROOT_DIR"
"$PYTHON_BIN" local_agent/local_agent.py &
AGENT_PID=$!

STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
STREAMLIT_SERVER_HEADLESS=true \
"$STREAMLIT_BIN" run local_agent/ui.py --server.address 0.0.0.0 --server.port "$LOCAL_AGENT_UI_PORT" >"$LOCAL_AGENT_UI_LOG_FILE" 2>&1 &
UI_PID=$!

wait "$AGENT_PID"
wait "$UI_PID"
