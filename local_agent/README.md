# local_agent

Local print worker + basic Python UI for Databricks-hosted queue jobs.

This runs **separately** from your current app and does not modify existing app files or behavior.

## Purpose

- Poll print jobs from a Databricks SQL/Delta-backed table
- Claim a job safely so only one agent handles it
- Send print to local Niimbot bridge (`niimblue-node`)
- Update job status (`queued -> claimed -> printed/queued/dead`)

## Folder isolation

Everything here is standalone:

- `local_agent/local_agent.py`
- `local_agent/requirements.txt`

The agent reads configuration from your existing repo root `.env`.

## Fast start (recommended)

1. In root `.env`, set `LOCAL_AGENT_PRINTER_ID` equal to `NIIMBOT_ADDRESS`.
2. Ensure Databricks credentials are set (`DATABRICKS_HOST`, `DATABRICKS_WAREHOUSE_ID`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET`).
3. Start worker:

```bash
cd local_agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python local_agent.py
```

4. (Optional) Start UI:

```bash
cd local_agent
source .venv/bin/activate
streamlit run ui.py
```

## Setup

```bash
cd local_agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Update the root `.env` with Databricks + printer values.

Suggested additional keys in root `.env`:

```dotenv
LOCAL_AGENT_QUEUE_TABLE=main.default.print_jobs
LOCAL_AGENT_PRINTER_ID=
LOCAL_AGENT_POLL_SECONDS=2
LOCAL_AGENT_CLAIM_TTL_SECONDS=30
LOCAL_AGENT_MAX_RETRY_BACKOFF_SECONDS=120
LOCAL_AGENT_DEFAULT_MAX_ATTEMPTS=5
LOCAL_AGENT_ID=onsite-agent-1
LOCAL_AGENT_AUTO_CONNECT=true

# Optional defaults if queue payload omits size/quantity
NIIMBOT_LABEL_WIDTH_PX=560
NIIMBOT_LABEL_HEIGHT_PX=320
NIIMBOT_DEFAULT_QUANTITY=1
NIIMBOT_PRINT_DIRECTION=top
```

Minimal practical values:

- `LOCAL_AGENT_LOCAL_RUN` = `true` to read local JSON queue instead of Databricks table
- `LOCAL_AGENT_LOCAL_QUEUE_FILE` = local queue path (default `app/backend/data/print_jobs.json`)
- `LOCAL_AGENT_QUEUE_TABLE` = queue table name used by your app
- `LOCAL_AGENT_ID` = unique identifier for this machine (for example `onsite-agent-1`)
- `LOCAL_AGENT_PRINTER_ID` = same as `NIIMBOT_ADDRESS` on this machine
- `NIIMBOT_SERVER_URL` = your local niimblue endpoint (for example `http://localhost:5050`)

## Run

```bash
cd local_agent
source .venv/bin/activate
python local_agent.py
```

## UI (basic)

```bash
cd local_agent
source .venv/bin/activate
streamlit run ui.py
```

UI supports:

- check printer bridge availability
- connect printer
- print next queued job
- view queued count and recent jobs

If the UI shows no jobs, confirm the queue table and printer targeting (`printer_id`) match this agent.

When `LOCAL_AGENT_LOCAL_RUN=true`, queue table settings are ignored and jobs are read from the local JSON queue file.

## Required queue table assumptions

The agent expects a table like `main.default.print_jobs` with at least these columns:

- `job_id STRING`
- `status STRING`
- `payload_json STRING`
- `created_at TIMESTAMP`
- `updated_at TIMESTAMP`
- `attempt_count INT`
- `max_attempts INT`
- `next_attempt_at TIMESTAMP`
- `printer_id STRING`
- `claimed_by STRING`
- `claimed_at TIMESTAMP`
- `claim_expires_at TIMESTAMP`
- `printed_at TIMESTAMP`
- `error_message STRING`

## Payload format

`payload_json` supports either:

### A) Ready-to-print payload

```json
{
  "imageBase64": "...",
  "labelWidth": 560,
  "labelHeight": 320,
  "printTask": "B1",
  "printDirection": "top",
  "quantity": 1
}
```

### B) Nested object

```json
{
  "printRequest": {
    "imageBase64": "...",
    "labelWidth": 560,
    "labelHeight": 320
  }
}
```

### C) URL-based image

```json
{
  "imageUrl": "https://.../tag.png",
  "labelWidth": 560,
  "labelHeight": 320
}
```

If `imageBase64` is missing and `imageUrl` is present, the agent downloads the image and prints it.

## Notes

- `LOCAL_AGENT_PRINTER_ID` is optional. If set, agent only picks jobs for that `printer_id` (or null jobs).
- If print fails, job is re-queued with backoff until `max_attempts`, then marked `dead`.
- Agent can auto-call `/connect` before printing when `LOCAL_AGENT_AUTO_CONNECT=true`.
