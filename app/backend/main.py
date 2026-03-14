"""FastAPI backend server for event registration and name tag printing."""

import base64
import json
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import httpx
from dotenv import load_dotenv
from PIL import Image

from backend import db
from backend.services.nametag import generate_nametag_image, LABEL_WIDTH_PX, LABEL_HEIGHT_PX

print("✅ All imports successful")

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
ALLOW_EMAIL_REUSE = os.getenv("ALLOW_EMAIL_REUSE", "true").lower() != "false"
NIIMBOT_SERVER_URL = os.getenv("NIIMBOT_SERVER_URL", "").rstrip("/")
EVENT_NAME = os.getenv("EVENT_NAME", "Energy & Utilities Connect Sydney")
EVENT_LOCATION = os.getenv("EVENT_LOCATION", "Sydney")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage app lifecycle: startup and shutdown."""
    print("\n🚀 FastAPI startup\n")
    
    try:
        await db.init_connection()
        print("✅ App startup complete\n")
    except Exception as e:
        print(f"⚠️  Database connection failed (will retry on first request): {e}\n")
    
    yield
    
    print("\n📴 FastAPI shutdown\n")
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
        print(f"✅ Found frontend files at {abs_path}")
        break

if frontend_dir:
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")
else:
    print(f"⚠️  Frontend directory not found in {root_candidates}")


IMAGES_DIR = Path(app_dir) / "data" / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

LOCAL_REGISTRATIONS_FILE = Path(app_dir) / "data" / "registrations.json"

app.mount("/images", StaticFiles(directory=str(IMAGES_DIR)), name="images")


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
        print(f"⚠️ Failed to read local registrations: {exc}")

    return []


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
        return {"available": False, "reason": "NIIMBOT_SERVER_URL is not configured."}

    transport = os.getenv("NIIMBOT_TRANSPORT")
    address = os.getenv("NIIMBOT_ADDRESS")
    if not transport or not address:
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
                return {"available": True, "reason": "Printer is reachable."}

            return {
                "available": False,
                "reason": f"Connect failed: {connect_text or connect_res.status_code}",
            }
    except Exception as exc:
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
        # Ensure connection is initialized (retry on first request)
        if db._connection is None:
            await db.init_connection()
        
        # Check if email already exists
        exists = await db.check_email_exists(email)
        if exists:
            if not ALLOW_EMAIL_REUSE:
                raise HTTPException(
                    status_code=409,
                    detail="You're already checked in with this email.",
                )
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
        print(f"❌ Registration error: {type(e).__name__}: {e}")
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
                print(f"⚠️ Could not attach nametag filename to registration: {exc}")

        printer = await _check_printer_available()

        printed = False
        printer_message = ""

        if printer["available"]:
            # Default behavior: print the generated image exactly as saved.
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
                    print(f"⚠️ Portrait rotation failed, printing original orientation: {rotate_exc}")

            image_base64 = base64.b64encode(print_buffer).decode()
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
                    print_res = await client.post(
                        f"{NIIMBOT_SERVER_URL}/print",
                        json={
                            "imageBase64": image_base64,
                            "labelWidth": print_label_width,
                            "labelHeight": print_label_height,
                            "printTask": os.getenv("NIIMBOT_PRINT_TASK", "B1"),
                            "printDirection": "top",
                            "quantity": 1,
                        },
                    )

                    if print_res.status_code == 200:
                        printed = True
                    else:
                        printer_message = f"Printer rejected job: {print_res.text}"
                        print(f"❌ Print failed: {print_res.text}")
            except Exception as exc:
                printer_message = f"Printer error: {exc}"
                print(f"❌ Print error: {exc}")
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
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Print error: {e}")
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
        return {"registrations": registrations, "count": len(registrations)}
    except Exception as e:
        print(f"❌ Failed to fetch registrations: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch registrations: {str(e)}")


@app.get("/api/admin/attendees")
async def get_admin_attendees():
    """Admin attendees list from local JSON storage with nametag image links."""
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
    return {"attendees": attendees, "count": len(attendees)}


@app.get("/")
async def root():
    """Serve the HTML frontend."""
    for candidate in root_candidates:
        html_file = os.path.join(candidate, "index.html")
        if os.path.exists(html_file):
            return FileResponse(html_file, media_type="text/html")
    return {"message": "Event Registration Backend API"}


@app.get("/admin")
async def admin_page():
    """Serve the admin attendees page."""
    for candidate in root_candidates:
        html_file = os.path.join(candidate, "admin.html")
        if os.path.exists(html_file):
            return FileResponse(html_file, media_type="text/html")
    raise HTTPException(status_code=404, detail="Admin page not found")
