"""FastAPI backend server for event registration and name tag printing."""

import base64
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import httpx
from dotenv import load_dotenv
from PIL import Image

from backend import db
from backend.services.nametag import generate_nametag_image, LABEL_WIDTH_PX, LABEL_HEIGHT_PX


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("event_registration.backend.main")

logger.info("Backend module imports completed")

load_dotenv()


# Request/Response models
class RegistrationRequest(BaseModel):
    """Event registration request."""

    firstName: str
    lastName: str
    company: str
    email: str
    contactPermission: bool = False


class PrintRequest(BaseModel):
    """Name tag print request."""

    firstName: str
    company: Optional[str] = ""
    groupName: Optional[str] = None
    location: Optional[str] = None
    registrationId: Optional[int] = None


# Global configuration
EMAIL_REGEX = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
NIIMBOT_SERVER_URL = os.getenv("NIIMBOT_SERVER_URL", "").rstrip("/")
EVENT_NAME = os.getenv("EVENT_NAME", "Energy & Utilities Connect Sydney")
EVENT_LOCATION = os.getenv("EVENT_LOCATION", "Sydney")
IS_DATABRICKS_APP = bool(os.getenv("DATABRICKS_APP_NAME"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage app lifecycle: startup and shutdown."""
    logger.info(
        "FastAPI startup | event_name=%s | event_location=%s | queue_table=%s",
        EVENT_NAME,
        EVENT_LOCATION,
        LOCAL_AGENT_QUEUE_TABLE or "<unset>",
    )
    
    try:
        await db.init_connection()
        logger.info("App startup complete")
    except Exception as e:
        logger.warning("Database init failed at startup; will retry on demand | error=%s", e)
    
    yield
    
    logger.info("FastAPI shutdown")
    await db.close_connection()


# Initialize FastAPI app
app = FastAPI(title="Event Registration Backend", lifespan=lifespan)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files - look for frontend files in various locations
app_dir = os.path.dirname(os.path.abspath(__file__))
root_candidates = [
    os.path.join(app_dir, "..", "frontend"),
    os.path.join(app_dir, "..", "frontend", "public"),
    os.path.join(app_dir, "frontend"),
    os.path.join(app_dir, "..", "public"),
    os.path.join(app_dir, "public"),
]

frontend_dir = None
for candidate in root_candidates:
    abs_path = os.path.abspath(candidate)
    if os.path.isdir(abs_path):
        frontend_dir = abs_path
        logger.info("Found frontend directory | path=%s", abs_path)
        break

if frontend_dir:
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")
else:
    logger.warning("Frontend directory not found | candidates=%s", root_candidates)


IMAGES_DIR = Path(app_dir) / "data" / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

LOCAL_REGISTRATIONS_FILE = Path(app_dir) / "data" / "registrations.json"
LOCAL_PRINT_JOBS_FILE = Path(app_dir) / "data" / "print_jobs.json"
LOCAL_AGENT_QUEUE_TABLE = os.getenv("LOCAL_AGENT_QUEUE_TABLE", "").strip()

app.mount("/images", StaticFiles(directory=str(IMAGES_DIR)), name="images")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log request lifecycle for easier Databricks runtime debugging."""
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    start = time.perf_counter()
    logger.info(
        "request.start | id=%s | method=%s | path=%s | client=%s",
        request_id,
        request.method,
        request.url.path,
        request.client.host if request.client else "unknown",
    )

    try:
        response = await call_next(request)
    except Exception:
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.exception(
            "request.error | id=%s | method=%s | path=%s | duration_ms=%s",
            request_id,
            request.method,
            request.url.path,
            duration_ms,
        )
        raise

    duration_ms = int((time.perf_counter() - start) * 1000)
    logger.info(
        "request.end | id=%s | method=%s | path=%s | status=%s | duration_ms=%s",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    response.headers["X-Request-ID"] = request_id
    return response


def _get_next_image_counter(images_dir: Path) -> int:
    """Get the next monotonically increasing image counter from saved files."""
    max_counter = 0
    for image_file in images_dir.glob("*.png"):
        name = image_file.name
        if "_" not in name:
            continue
        maybe_counter = name.split("_", 1)[0]
        if maybe_counter.isdigit():
            max_counter = max(max_counter, int(maybe_counter))
    return max_counter + 1


def _save_nametag_image(
    image_buffer: bytes, name_display: str, registration_id: Optional[int] = None
) -> tuple[int, str, str]:
    """Save name tag image to local file storage with incremental counter prefix."""
    counter = registration_id if registration_id is not None else _get_next_image_counter(IMAGES_DIR)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_name = name_display.replace(" ", "_")
    filename = f"{counter}_nametag_{safe_name}_{timestamp}.png"
    file_path = IMAGES_DIR / filename
    file_path.write_bytes(image_buffer)
    return counter, filename, str(file_path)


def _load_local_registrations() -> list[dict]:
    """Load registrations from local JSON file used by file fallback storage."""
    if not LOCAL_REGISTRATIONS_FILE.exists():
        return []

    try:
        with LOCAL_REGISTRATIONS_FILE.open("r", encoding="utf-8") as file_handle:
            data = json.load(file_handle)
            if isinstance(data, list):
                return data
    except Exception as exc:
        logger.warning("Failed to read local registrations | error=%s", exc)

    return []


def _load_local_print_jobs() -> list[dict]:
    """Load local print queue jobs from JSON file."""
    if not LOCAL_PRINT_JOBS_FILE.exists():
        return []

    try:
        with LOCAL_PRINT_JOBS_FILE.open("r", encoding="utf-8") as file_handle:
            data = json.load(file_handle)
            if isinstance(data, list):
                return data
    except Exception as exc:
        logger.warning("Failed to read local print jobs | error=%s", exc)

    return []


def _save_local_print_jobs(print_jobs: list[dict]) -> None:
    """Persist local print queue jobs to JSON file."""
    LOCAL_PRINT_JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCAL_PRINT_JOBS_FILE.open("w", encoding="utf-8") as file_handle:
        json.dump(print_jobs, file_handle, indent=2, ensure_ascii=False)


def _enqueue_local_print_job(
    print_payload: dict,
    registration_id: Optional[int],
    name_display: str,
    company_val: str,
    group: str,
    location: str,
    png_filename: str,
) -> str:
    """Append one local-agent-compatible print job record to local JSON queue."""
    print_jobs = _load_local_print_jobs()
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    job_id = f"job_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}_{uuid.uuid4().hex[:8]}"

    try:
        max_attempts = int(os.getenv("LOCAL_AGENT_DEFAULT_MAX_ATTEMPTS", "5"))
    except ValueError:
        max_attempts = 5

    printer_id = (
        os.getenv("LOCAL_AGENT_PRINTER_ID")
        or os.getenv("NIIMBOT_ADDRESS")
        or None
    )

    payload = {
        "printRequest": print_payload,
        "registrationId": registration_id,
        "name": name_display,
        "company": company_val,
        "groupName": group,
        "location": location,
        "filename": png_filename,
    }

    print_jobs.append(
        {
            "job_id": job_id,
            "status": "queued",
            "payload_json": json.dumps(payload, ensure_ascii=False),
            "created_at": now_iso,
            "updated_at": now_iso,
            "attempt_count": 0,
            "max_attempts": max(1, max_attempts),
            "next_attempt_at": None,
            "printer_id": printer_id,
            "claimed_by": None,
            "claimed_at": None,
            "claim_expires_at": None,
            "printed_at": None,
            "error_message": None,
        }
    )
    _save_local_print_jobs(print_jobs)
    logger.info("Queued local print job | job_id=%s | registration_id=%s", job_id, registration_id)
    return job_id


def _is_valid_table_identifier(table_name: str) -> bool:
    """Allow only simple 3-part UC identifiers: catalog.schema.table."""
    return bool(re.match(r"^[A-Za-z0-9_]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+$", table_name or ""))


def _should_enqueue_to_table() -> bool:
    """Use Databricks queue table when Databricks credentials + queue table are present."""
    return bool(
        not db._use_file_storage
        and
        os.getenv("DATABRICKS_HOST")
        and os.getenv("DATABRICKS_WAREHOUSE_ID")
        and LOCAL_AGENT_QUEUE_TABLE
        and _is_valid_table_identifier(LOCAL_AGENT_QUEUE_TABLE)
    )


async def _enqueue_databricks_print_job(
    print_payload: dict,
    registration_id: Optional[int],
    name_display: str,
    company_val: str,
    group: str,
    location: str,
    png_filename: str,
) -> str:
    """Insert one local-agent-compatible print job record into Databricks queue table."""
    if not _is_valid_table_identifier(LOCAL_AGENT_QUEUE_TABLE):
        raise RuntimeError(
            "LOCAL_AGENT_QUEUE_TABLE must be a valid 3-part identifier (catalog.schema.table)"
        )

    if db._connection is None:
        await db.init_connection()

    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    job_id = f"job_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}_{uuid.uuid4().hex[:8]}"

    try:
        max_attempts = int(os.getenv("LOCAL_AGENT_DEFAULT_MAX_ATTEMPTS", "5"))
    except ValueError:
        max_attempts = 5

    printer_id = os.getenv("LOCAL_AGENT_PRINTER_ID") or os.getenv("NIIMBOT_ADDRESS") or None

    payload = {
        "printRequest": print_payload,
        "registrationId": registration_id,
        "name": name_display,
        "company": company_val,
        "groupName": group,
        "location": location,
        "filename": png_filename,
    }

    payload_json = json.dumps(payload, ensure_ascii=False)

    query = f"""
        INSERT INTO {LOCAL_AGENT_QUEUE_TABLE}
        (
            job_id,
            status,
            payload_json,
            created_at,
            updated_at,
            attempt_count,
            max_attempts,
            next_attempt_at,
            printer_id,
            claimed_by,
            claimed_at,
            claim_expires_at,
            printed_at,
            error_message
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    await db.execute_query(
        query,
        [
            job_id,
            "queued",
            payload_json,
            now_iso,
            now_iso,
            0,
            max(1, max_attempts),
            None,
            printer_id,
            None,
            None,
            None,
            None,
            None,
        ],
    )

    logger.info(
        "Queued Databricks print job | table=%s | job_id=%s | registration_id=%s",
        LOCAL_AGENT_QUEUE_TABLE,
        job_id,
        registration_id,
    )
    return job_id


async def _enqueue_print_job(
    print_payload: dict,
    registration_id: Optional[int],
    name_display: str,
    company_val: str,
    group: str,
    location: str,
    png_filename: str,
) -> str:
    """Enqueue print job to Databricks queue table (preferred) or local JSON queue."""
    if IS_DATABRICKS_APP:
        if not _is_valid_table_identifier(LOCAL_AGENT_QUEUE_TABLE):
            raise RuntimeError(
                "Databricks App mode requires LOCAL_AGENT_QUEUE_TABLE "
                "as a valid 3-part identifier (catalog.schema.table)."
            )
        logger.info(
            "Queue strategy selected | target=databricks_table | table=%s | mode=delta_only",
            LOCAL_AGENT_QUEUE_TABLE,
        )
        return await _enqueue_databricks_print_job(
            print_payload=print_payload,
            registration_id=registration_id,
            name_display=name_display,
            company_val=company_val,
            group=group,
            location=location,
            png_filename=png_filename,
        )

    if _should_enqueue_to_table():
        logger.info("Queue strategy selected | target=databricks_table | table=%s", LOCAL_AGENT_QUEUE_TABLE)
        return await _enqueue_databricks_print_job(
            print_payload=print_payload,
            registration_id=registration_id,
            name_display=name_display,
            company_val=company_val,
            group=group,
            location=location,
            png_filename=png_filename,
        )

    logger.info("Queue strategy selected | target=local_file | file=%s", str(LOCAL_PRINT_JOBS_FILE))
    return _enqueue_local_print_job(
        print_payload=print_payload,
        registration_id=registration_id,
        name_display=name_display,
        company_val=company_val,
        group=group,
        location=location,
        png_filename=png_filename,
    )


def _find_latest_nametag_for_first_name(first_name: str) -> Optional[str]:
    """Best-effort match: latest nametag image for first name based on filename."""
    safe_name = (first_name or "").strip().upper().replace(" ", "_")
    if not safe_name:
        return None

    pattern = f"*_nametag_{safe_name}_*.png"
    matches = list(IMAGES_DIR.glob(pattern))
    if not matches:
        return None

    latest = max(matches, key=lambda path: path.stat().st_mtime)
    return latest.name


def _get_first_name_for_match(registration: dict) -> str:
    """Extract first name from either first_name or legacy name field."""
    first_name = str(registration.get("first_name") or "").strip()
    if first_name:
        return first_name

    full_name = str(registration.get("name") or "").strip()
    if full_name:
        return full_name.split(" ")[0]

    return ""


async def _check_printer_available() -> dict:
    """Check whether printer service is reachable and printer can be connected."""
    if not NIIMBOT_SERVER_URL:
        logger.info("Printer unavailable | reason=NIIMBOT_SERVER_URL not configured")
        return {"available": False, "reason": "NIIMBOT_SERVER_URL is not configured."}

    transport = os.getenv("NIIMBOT_TRANSPORT")
    address = os.getenv("NIIMBOT_ADDRESS")
    if not transport or not address:
        logger.info("Printer unavailable | reason=transport/address not configured")
        return {
            "available": False,
            "reason": "NIIMBOT_TRANSPORT or NIIMBOT_ADDRESS is not configured.",
        }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0)) as client:
            connect_res = await client.post(
                f"{NIIMBOT_SERVER_URL}/connect",
                json={"transport": transport, "address": address},
            )
            connect_text = connect_res.text or ""

            if connect_res.status_code == 200 or "Already connected" in connect_text:
                logger.info("Printer reachable | transport=%s | address=%s", transport, address)
                return {"available": True, "reason": "Printer is reachable."}

            return {
                "available": False,
                "reason": f"Connect failed: {connect_text or connect_res.status_code}",
            }
    except Exception as exc:
        logger.warning("Printer check failed | error=%s", exc)
        return {"available": False, "reason": f"Printer service unreachable: {exc}"}


def is_valid_email(email: str) -> bool:
    """Validate email format."""
    return EMAIL_REGEX.match(email) is not None


def sanitize_input(text: str, max_length: int = 255) -> str:
    """Sanitize and validate input."""
    if not isinstance(text, str):
        return ""
    return text.strip()[:max_length]


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    logger.info("Health check requested")
    return {"status": "ok"}


@app.get("/api/printer-status")
async def printer_status():
    """Get current printer availability state for frontend UX."""
    status = await _check_printer_available()
    return {
        "available": status["available"],
        "message": status["reason"],
        "serverUrl": NIIMBOT_SERVER_URL or None,
        "transport": os.getenv("NIIMBOT_TRANSPORT") or None,
        "address": os.getenv("NIIMBOT_ADDRESS") or None,
    }


@app.get("/api/config")
async def app_config():
    """Get frontend config values loaded from environment."""
    logger.info("Config endpoint requested")
    return {
        "eventName": EVENT_NAME,
        "eventLocation": EVENT_LOCATION,
    }


@app.post("/api/register")
async def register(req: RegistrationRequest):
    """Register a new attendee."""
    
    # Validate required fields
    if not all([req.firstName, req.lastName, req.company, req.email]):
        raise HTTPException(status_code=400, detail="All text fields are required.")

    # Sanitize inputs
    first_name = sanitize_input(req.firstName)
    last_name = sanitize_input(req.lastName)
    company = sanitize_input(req.company)
    email = sanitize_input(req.email).lower()

    # Validate sanitized inputs aren't empty
    if not all([first_name, last_name, company, email]):
        raise HTTPException(status_code=400, detail="All text fields are required.")

    # Validate email format
    if not is_valid_email(email):
        raise HTTPException(status_code=400, detail="Invalid email format.")

    try:
        logger.info("Registration attempt | email_domain=%s", (req.email.split("@")[-1] if "@" in req.email else "invalid"))
        # Ensure connection is initialized (retry on first request)
        if db._connection is None:
            await db.init_connection()
        
        # Check if email already exists
        exists = await db.check_email_exists(email)
        if exists:
            logger.info("Registration already exists | email_domain=%s", (email.split("@")[-1] if "@" in email else "invalid"))
            return {"message": "Check-in successful! Welcome back."}

        # Save registration
        registration_id = await db.save_registration(
            first_name=first_name,
            last_name=last_name,
            email=email,
            company=company,
            contact_permission=req.contactPermission,
        )

        return {
            "message": "Check-in successful! Welcome.",
            "registrationId": registration_id,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Registration error | error_type=%s | error=%s", type(e).__name__, e)
        raise HTTPException(
            status_code=500,
            detail=f"Registration failed: {str(e)}",
        )



@app.post("/api/print")
async def print_nametag(req: PrintRequest):
    """Generate, save, and optionally print a name tag."""
    
    if not req.firstName:
        raise HTTPException(status_code=400, detail="First name is required for printing.")

    name_display = req.firstName.strip().upper()
    company_val = (req.company or "").strip()
    group = (req.groupName or EVENT_NAME).strip()
    location = (req.location or EVENT_LOCATION).strip()

    try:
        logger.info("Print request received | registration_id=%s | first_name_len=%s", req.registrationId, len(req.firstName or ""))
        # Generate PNG image
        image_buffer = generate_nametag_image(
            {
                "name": name_display,
                "company": company_val,
                "groupName": group,
                "location": location,
            }
        )

        image_id, png_filename, saved_path = _save_nametag_image(
            image_buffer,
            name_display,
            registration_id=req.registrationId,
        )

        if req.registrationId is not None:
            try:
                await db.attach_nametag_filename(req.registrationId, png_filename)
            except Exception as exc:
                logger.warning("Could not attach nametag filename | registration_id=%s | error=%s", req.registrationId, exc)

        # Default print payload: generated image exactly as saved.
        print_buffer = image_buffer
        print_label_width = LABEL_WIDTH_PX
        print_label_height = LABEL_HEIGHT_PX

        if LABEL_HEIGHT_PX > LABEL_WIDTH_PX:
            # Portrait labels (e.g. 50x80) are rotated for printer output only.
            # Saved images and browser previews intentionally stay unrotated.
            try:
                with Image.open(BytesIO(image_buffer)) as portrait_img:
                    rotated = portrait_img.rotate(-90, expand=True)
                    rotated_buffer = BytesIO()
                    rotated.save(rotated_buffer, format="PNG")
                    # Printer receives landscape-oriented pixels + swapped dimensions.
                    print_buffer = rotated_buffer.getvalue()
                    print_label_width = LABEL_HEIGHT_PX
                    print_label_height = LABEL_WIDTH_PX
            except Exception as rotate_exc:
                logger.warning("Portrait rotation failed; using original orientation | error=%s", rotate_exc)

        image_base64 = base64.b64encode(print_buffer).decode()
        print_payload = {
            "imageBase64": image_base64,
            "labelWidth": print_label_width,
            "labelHeight": print_label_height,
            "printTask": os.getenv("NIIMBOT_PRINT_TASK", "B1"),
            "printDirection": "top",
            "quantity": 1,
        }

        queue_job_id: Optional[str] = None
        try:
            queue_job_id = await _enqueue_print_job(
                print_payload=print_payload,
                registration_id=req.registrationId,
                name_display=name_display,
                company_val=company_val,
                group=group,
                location=location,
                png_filename=png_filename,
            )
        except Exception as queue_exc:
            # Keep current user flow unchanged if queue-write fails.
            logger.warning("Failed to enqueue print job | error=%s", queue_exc)

        printer = await _check_printer_available()

        printed = False
        printer_message = ""

        if printer["available"]:
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
                    print_res = await client.post(
                        f"{NIIMBOT_SERVER_URL}/print",
                        json=print_payload,
                    )

                    if print_res.status_code == 200:
                        printed = True
                        logger.info("Printer accepted print job | registration_id=%s", req.registrationId)
                    else:
                        printer_message = f"Printer rejected job: {print_res.text}"
                        logger.warning("Printer rejected job | status=%s | body=%s", print_res.status_code, print_res.text)
            except Exception as exc:
                printer_message = f"Printer error: {exc}"
                logger.warning("Printer request failed | error=%s", exc)
        else:
            printer_message = printer["reason"]

        message = (
            "Name tag saved and sent to printer successfully."
            if printed
            else "Name tag saved. Printer not available, print skipped."
        )

        return {
            "message": message,
            "data": {
                "imageId": image_id,
                "filename": png_filename,
                "savedPath": saved_path,
                "printed": printed,
                "printerMessage": printer_message,
                "groupName": group,
                "location": location,
                "name": name_display,
                "company": company_val,
                "registrationId": req.registrationId,
                "queueJobId": queue_job_id,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unhandled print error | error=%s", e)
        raise HTTPException(
            status_code=500,
            detail="An error occurred while printing the name tag.",
        )


@app.get("/api/registrations")
async def get_registrations(limit: Optional[int] = None):
    """Get all registrations (for admin view)."""
    try:
        # Ensure connection is initialized
        if db._connection is None:
            await db.init_connection()
        
        registrations = await db.get_registrations(limit=limit)
        logger.info("Fetched registrations | count=%s | limit=%s", len(registrations), limit)
        return {"registrations": registrations, "count": len(registrations)}
    except Exception as e:
        logger.exception("Failed to fetch registrations | error=%s", e)
        raise HTTPException(status_code=500, detail=f"Failed to fetch registrations: {str(e)}")


@app.get("/api/admin/attendees")
async def get_admin_attendees():
    """Admin attendees list with nametag image links."""
    if IS_DATABRICKS_APP:
        if db._connection is None:
            await db.init_connection()
        registrations = await db.get_registrations(limit=None)
    else:
        registrations = _load_local_registrations()

    attendees: list[dict] = []

    for registration in registrations:
        image_filename = registration.get("nametag_image_filename")
        if not image_filename:
            first_name = _get_first_name_for_match(registration)
            image_filename = _find_latest_nametag_for_first_name(first_name)

        attendees.append(
            {
                **registration,
                "nametag_image_filename": image_filename,
                "nametag_image_url": f"/images/{image_filename}" if image_filename else None,
            }
        )

    attendees.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
    logger.info("Fetched admin attendees | count=%s", len(attendees))
    return {"attendees": attendees, "count": len(attendees)}


@app.get("/")
async def root():
    """Serve the HTML frontend."""
    for candidate in root_candidates:
        html_file = os.path.join(candidate, "index.html")
        if os.path.exists(html_file):
            logger.info("Serving root HTML | path=%s", html_file)
            return FileResponse(html_file, media_type="text/html")
    logger.warning("Root HTML not found; returning API fallback message")
    return {"message": "Event Registration Backend API"}


@app.get("/admin")
async def admin_page():
    """Serve the admin attendees page."""
    for candidate in root_candidates:
        html_file = os.path.join(candidate, "admin.html")
        if os.path.exists(html_file):
            logger.info("Serving admin HTML | path=%s", html_file)
            return FileResponse(html_file, media_type="text/html")
    logger.warning("Admin HTML not found")
    raise HTTPException(status_code=404, detail="Admin page not found")
