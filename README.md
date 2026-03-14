# Event Registration App

A simple event registration application built with Node.js, Express, and PostgreSQL.

## Features

- User registration form with validation
- PostgreSQL database storage
- Responsive UI
- Email validation
- Contact permission opt-in

## Prerequisites

- Node.js (v14 or higher)
- npm or yarn
- **Optional:** PostgreSQL database (if not provided, the app will use file-based storage)

## Setup

1. **Install dependencies:**

   ```bash
   npm install
   ```

2. **Choose your storage option:**

   **Option A: File-based storage (No database required - recommended for testing)**
   - Simply start the app! No configuration needed.
   - Data will be stored in `./data/registrations.json`
   - Perfect for local development and testing

   **Option B: PostgreSQL database**
   - Create a PostgreSQL database
   - Run the SQL schema (see `schema.sql`)
   - Copy `.env.example` to `.env`
   - Fill in your database credentials:
     ```
     DB_USER=your_db_user
     DB_HOST=localhost
     DB_NAME=your_db_name
     DB_PASSWORD=your_db_password
     DB_PORT=5432
     PORT=8000
     ```

   **Note:** The app automatically detects if PostgreSQL credentials are provided. If not, it falls back to file-based storage. The app uses `dotenv` to automatically load environment variables from `.env` file in local development. In production (e.g., Databricks Apps), set these variables in your deployment environment.

## Running Locally

1. **Start the server:**

   ```bash
   npm start
   ```

   The app will automatically:
   - Use file-based storage if no PostgreSQL credentials are found
   - Use PostgreSQL if credentials are provided in `.env` file

2. **Open your browser:**
   Navigate to `http://localhost:8000`

### File-based Storage

When using file-based storage:

- All registrations are stored in `./data/registrations.json`
- No database setup required
- Data persists between server restarts
- Perfect for testing and development

## Database Schema

The app expects a table named `event_registrations` with the following structure:

- `id` (SERIAL PRIMARY KEY)
- `first_name` (VARCHAR)
- `last_name` (VARCHAR)
- `company` (VARCHAR)
- `company_email` (VARCHAR)
- `contact_permission` (BOOLEAN)
- `created_at` (TIMESTAMP DEFAULT NOW())

See `schema.sql` for the complete schema.

## Project Structure

```
event-registration-app/
├── server.js          # Express server and API endpoints
├── storage.js         # File-based storage implementation
├── lib/
│   └── nametag-image.js  # Name tag image generator (sharp + SVG)
├── public/
│   └── index.html     # Frontend registration form
├── data/              # File storage directory (created automatically)
│   └── registrations.json
├── package.json       # Dependencies and scripts
├── schema.sql         # Database schema (for PostgreSQL)
└── README.md          # This file
```

## API Endpoints

- `POST /api/register` - Register a new event attendee
  - Body: `{ firstName, lastName, company, email, contactPermission }`
  - Returns: `{ message: "Registration successful!" }` or error
- `POST /api/print` - Print name tag (uses [@mmote/niimbluelib](https://www.npmjs.com/package/@mmote/niimbluelib) via niimblue-node server)
  - Body: `{ firstName, company, groupName?, location? }`
  - Returns: `{ message, data }` or error

## Printing (Niimbot B3S)

Name tag printing uses **@mmote/niimbluelib** and generates an env-configured landscape image. Current default is **70mm × 40mm** (`NIIMBOT_LABEL_WIDTH_MM`, `NIIMBOT_LABEL_HEIGHT_MM`) and is converted to printer-safe pixels.

### Quick start scripts

App only (backend + frontend, no printer):

```bash
bash scripts/start_quick_app.sh
```

All services (printer + backend + frontend, no test print):

```bash
bash scripts/start_quick_all.sh
```

### One-command local startup (recommended)

1. Copy the config template:
   ```bash
   cp .env.example .env
   ```
2. Edit `.env` and set `NIIMBOT_ADDRESS`.
   - On macOS, use the Bluetooth device name (for example `B3S-XXXXXXXXXX`), not a MAC address.
3. Run:
   ```bash
   bash scripts/start_local_print.sh
   ```

For automatic startup + immediate test label:

```bash
bash scripts/start_local_print_with_test.sh
```

This starts the niimblue server, connects your printer, and starts FastAPI on `http://localhost:8000`.

### Manual startup

1. **Run the niimblue-node server** (uses niimbluelib):
   ```bash
   npx -y @mmote/niimblue-node server
   ```
2. **Connect to your printer** (via the server’s `/connect` or CLI).
3. **Configure env** (optional): set `NIIMBOT_SERVER_URL=http://localhost:5050`, and optionally `NIIMBOT_TRANSPORT`, `NIIMBOT_ADDRESS`, `NIIMBOT_PRINT_TASK`. See `.env.example`.

If `NIIMBOT_SERVER_URL` is not set, the app still generates the name tag image and returns success (no printer is used).

## Notes

- The app is configured to work with Databricks Apps (binds to 0.0.0.0)
- **File-based storage:** Works out of the box - no database setup needed!
- **PostgreSQL:** For production use, set up PostgreSQL and configure environment variables
- The app automatically chooses the storage method based on available configuration
