#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"
APP_YAML="$ROOT_DIR/app/app.yaml"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE"
  exit 1
fi

if ! command -v databricks >/dev/null 2>&1; then
  echo "Databricks CLI is required (command: databricks)."
  exit 1
fi

DEPLOY_PROFILE="${DATABRICKS_DEPLOY_PROFILE:-DEFAULT}"

set -a
source "$ENV_FILE"
set +a

required_vars=(
  EVENT_NAME
  EVENT_LOCATION
  DATABRICKS_CATALOG
  DATABRICKS_SCHEMA
  LOCAL_AGENT_QUEUE_TABLE
)

for var_name in "${required_vars[@]}"; do
  if [[ -z "${!var_name:-}" ]]; then
    echo "Missing required env var in .env: $var_name"
    exit 1
  fi
done

yaml_quote() {
  local value="$1"
  value=${value//\'/\'\'}
  printf "'%s'" "$value"
}

event_name_yaml="$(yaml_quote "$EVENT_NAME")"
event_location_yaml="$(yaml_quote "$EVENT_LOCATION")"
databricks_catalog_yaml="$(yaml_quote "$DATABRICKS_CATALOG")"
databricks_schema_yaml="$(yaml_quote "$DATABRICKS_SCHEMA")"
local_agent_queue_table_yaml="$(yaml_quote "$LOCAL_AGENT_QUEUE_TABLE")"

echo "Updating $APP_YAML from .env values..."
cat > "$APP_YAML" <<EOF
command:
  - "uvicorn"
  - "backend.main:app"
  - "--app-dir"
  - "."
  - "--host"
  - "0.0.0.0"
  - "--port"
  - "8000"

env:
  - name: DATABRICKS_WAREHOUSE_ID
    valueFrom: "app-warehouse"
  - name: DATABRICKS_CATALOG
    value: $databricks_catalog_yaml
  - name: DATABRICKS_SCHEMA
    value: $databricks_schema_yaml
  - name: DATABRICKS_VOLUME_PATH
    valueFrom: "app-volume"
  - name: EVENT_NAME
    value: $event_name_yaml
  - name: EVENT_LOCATION
    value: $event_location_yaml
  - name: LOCAL_AGENT_QUEUE_TABLE
    value: $local_agent_queue_table_yaml
  - name: PORT
    value: "8000"
EOF

echo "Deploying Databricks app bundle..."
cd "$ROOT_DIR"
databricks bundle deploy -t development --profile "$DEPLOY_PROFILE"
databricks bundle run -t development event-registration-app --profile "$DEPLOY_PROFILE"

echo "Deployment complete."
