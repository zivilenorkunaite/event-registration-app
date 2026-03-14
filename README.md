# Event Registration App

Event check-in app with:

- **FastAPI backend** (`app/backend`)
- **Vite frontend** (`app/frontend`)
- **Optional Niimbot printing** through `@mmote/niimblue-node`
- **Databricks SQL or local JSON storage fallback**

## What it does

- Attendee check-in form with validation
- Admin attendee table with search/filter/sort
- CSV export from admin view
- Nametag generation as PNG
- Optional live printer send
- Admin reprint button per attendee

## Tech stack

- Python: FastAPI, uvicorn, Pillow, httpx, python-dotenv
- Frontend: HTML/CSS/JS with Vite dev server
- Optional printer bridge: `@mmote/niimblue-node`

---

## Quick start

### 1) Prepare environment

From repo root:

```bash
cp .env.example .env
```

Edit `.env` as needed (minimum app defaults already work).

### 2) Install dependencies

Python:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r app/requirements.txt
```

Frontend:

```bash
cd app/frontend
npm install
cd ../..
```

### 3) Run app (no printer)

```bash
bash scripts/start_quick_app.sh
```

App URLs:

- Frontend: `http://localhost:8080`
- Backend API: `http://localhost:8000`
- Admin page: `http://localhost:8080/admin`

---

## Printing modes

### App only (no printer)

```bash
bash scripts/start_quick_app.sh
```

### App + printer bridge

```bash
bash scripts/start_quick_all.sh
```

### Printer-focused local startup (backend + printer)

```bash
bash scripts/start_local_print.sh
```

### Same as above + immediate test print

```bash
bash scripts/start_local_print_with_test.sh
```

> Tip (macOS): `NIIMBOT_ADDRESS` should be the Bluetooth device name from scan output.

---

## Local agent (Databricks queue -> local printer)

Use this when your main app is hosted remotely (for example Databricks Apps) but printing must happen on a local machine.

### Minimum config in `.env`

- Databricks SQL credentials (`DATABRICKS_HOST`, `DATABRICKS_WAREHOUSE_ID`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET`)
- Local printer bridge settings (`NIIMBOT_SERVER_URL`, `NIIMBOT_TRANSPORT`, `NIIMBOT_ADDRESS`)
- Local agent settings (`LOCAL_AGENT_QUEUE_TABLE`, `LOCAL_AGENT_ID`)

Recommended:

- Set `LOCAL_AGENT_PRINTER_ID` to the same value as `NIIMBOT_ADDRESS` so the agent only claims jobs intended for this printer.

### Run local agent worker

```bash
cd local_agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python local_agent.py
```

### Run local agent UI

```bash
cd local_agent
source .venv/bin/activate
streamlit run ui.py
```

UI is useful for manual operations (connect printer, print next job, inspect queue).

---

## Environment variables

See `.env.example` for the full list.

### Core

- `EVENT_NAME`
- `EVENT_LOCATION`

### Niimbot (optional)

- `NIIMBOT_SERVER_URL` (for example `http://localhost:5050`)
- `NIIMBOT_TRANSPORT` (typically `ble`)
- `NIIMBOT_ADDRESS`
- `NIIMBOT_PRINT_TASK` (default `B1`)
- `NIIMBOT_LABEL_WIDTH_MM`
- `NIIMBOT_LABEL_HEIGHT_MM`

### Databricks SQL (optional)

If these are unset, app uses local JSON fallback:

- `DATABRICKS_HOST`
- `DATABRICKS_WAREHOUSE_ID`
- `DATABRICKS_CLIENT_ID`
- `DATABRICKS_CLIENT_SECRET`
- `DATABRICKS_CATALOG` (optional)
- `DATABRICKS_SCHEMA` (optional)
- `DATABRICKS_VOLUME_PATH` (optional)

### Local agent (optional)

- `LOCAL_AGENT_QUEUE_TABLE` (for example `main.default.print_jobs`)
- `LOCAL_AGENT_PRINTER_ID` (recommended = `NIIMBOT_ADDRESS`)
- `LOCAL_AGENT_POLL_SECONDS`
- `LOCAL_AGENT_CLAIM_TTL_SECONDS`
- `LOCAL_AGENT_MAX_RETRY_BACKOFF_SECONDS`
- `LOCAL_AGENT_DEFAULT_MAX_ATTEMPTS`
- `LOCAL_AGENT_ID`
- `LOCAL_AGENT_AUTO_CONNECT`
- `NIIMBOT_LABEL_WIDTH_PX` / `NIIMBOT_LABEL_HEIGHT_PX` (fallback when payload omits size)
- `NIIMBOT_DEFAULT_QUANTITY` / `NIIMBOT_PRINT_DIRECTION` (fallback defaults)

---

## Storage behavior

- **With Databricks creds**: uses Databricks SQL connection.
- **Without Databricks creds**: uses local file storage (`app/backend/data/registrations.json`).
- Nametag images are saved under `app/backend/data/images` and served from `/images/...`.

---

## API endpoints

### Health

- `GET /health`

### Config and status

- `GET /api/config`
- `GET /api/printer-status`

### Registration + print

- `POST /api/register`
  - Body: `{ firstName, lastName, company, email, contactPermission }`
- `POST /api/print`
  - Body: `{ firstName, company?, groupName?, location?, registrationId? }`

### Admin data

- `GET /api/registrations`
- `GET /api/admin/attendees`

### Pages

- `GET /` (check-in page)
- `GET /admin` (admin page)

---

## Orientation behavior for labels

- Label size comes from `NIIMBOT_LABEL_WIDTH_MM` and `NIIMBOT_LABEL_HEIGHT_MM`.
- Generated/saved nametag image keeps normal layout.
- **Print payload only** auto-rotates when configured label is portrait (`height > width`) so devices like `50 x 80` print correctly.
- Website preview is not rotated.

---

## Git safety notes

- `.env` is ignored by git (safe for local secrets/device values).
- Keep real credentials only in `.env`, never in tracked files.
- `scripts/printer_test_direct.py` now reads printer address from env (`NIIMBOT_ADDRESS`) instead of hardcoded device IDs.

---

## Repo layout (current)

```text
event-registration-app/
├── .env.example
├── scripts/
│   ├── start_quick_app.sh
│   ├── start_quick_all.sh
│   ├── start_local_print.sh
│   ├── start_local_print_with_test.sh
│   └── printer_test_direct.py
└── app/
    ├── requirements.txt
    ├── backend/
    │   ├── main.py
    │   ├── db.py
    │   └── services/
    │       └── nametag.py
    └── frontend/
        ├── index.html
        └── admin.html
```
