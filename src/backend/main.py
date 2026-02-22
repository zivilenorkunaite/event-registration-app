"""FastAPI backend server for event registration and name tag printing."""

import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

print("📚 [Backend] Loading FastAPI...")
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr
print("📚 [Backend] Loading storage modules...")

from backend.storage.base import StorageBase
from backend.storage.databricks import DeltaStorage
from backend.storage.file import FileStorage
from backend.services.nametag import generate_nametag_image, LABEL_WIDTH_PX, LABEL_HEIGHT_PX
import httpx

print("📚 [Backend] All imports successful")


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
    groupName: Optional[str] = "Energy & Utilities Data Connect"
    location: Optional[str] = "Sydney"


class ErrorResponse(BaseModel):
    """Error response."""

    error: str


class SuccessResponse(BaseModel):
    """Success response."""

    message: str
    data: Optional[dict] = None


# Global storage instance
storage: StorageBase = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage app lifecycle: startup and shutdown."""
    print("\n🚀 FastAPI lifespan: STARTUP\n")
    # Note: Storage initialization is now lazy (happens on first use)
    # This allows the app to start quickly even if database is unavailable
    print("✅ App startup complete (storage will initialize on first use)\n")
    
    yield
    
    # Shutdown
    print("\n📴 FastAPI lifespan: SHUTDOWN\n")
    if storage:
        await storage.close()


# Initialize FastAPI app
app = FastAPI(title="Event Registration Backend", lifespan=lifespan)

# CORS middleware for cross-origin requests from frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files (serve index.html and CSS/JS from public/ directory)
# Support both local dev and Databricks deployment paths
backend_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.dirname(backend_dir)
public_dir = os.path.join(src_dir, "public")
public_dir_abs = os.path.abspath(public_dir)
print(f"📁 Backend dir: {backend_dir}")
print(f"📁 Src dir: {src_dir}")
print(f"📁 Looking for static files at: {public_dir_abs}")
if os.path.isdir(public_dir_abs):
    print(f"✅ Found public directory, mounting static files")
    try:
        app.mount("/static", StaticFiles(directory=public_dir_abs), name="static")
        print(f"✅ Static files mounted successfully")
    except Exception as e:
        print(f"⚠️  Error mounting static files: {e}")
else:
    print(f"⚠️  Static files directory not found at: {public_dir_abs}")

# Configuration
EMAIL_REGEX = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
ALLOW_EMAIL_REUSE = os.getenv("ALLOW_EMAIL_REUSE", "true").lower() != "false"
NIIMBOT_SERVER_URL = os.getenv("NIIMBOT_SERVER_URL", "").rstrip("/")


def is_valid_email(email: str) -> bool:
    """Validate email format."""
    return EMAIL_REGEX.match(email) is not None


def sanitize_input(text: str, max_length: int = 255) -> str:
    """Sanitize and validate input."""
    if not isinstance(text, str):
        return ""
    return text.strip()[:max_length]


async def initialize_storage() -> None:
    """Initialize storage backend (idempotent - safe to call multiple times)."""
    global storage
    
    # Skip if already initialized
    if storage is not None:
        print(f"   ✓ Storage already initialized: {storage.__class__.__name__}")
        return

    print("\n📊 Storage Initialization Debug Info:")
    print(f"   DATABRICKS_HOST: {bool(os.getenv('DATABRICKS_HOST'))}")
    print(f"   DATABRICKS_WAREHOUSE_ID: {bool(os.getenv('DATABRICKS_WAREHOUSE_ID'))}")
    print(f"   DATABRICKS_CLIENT_ID: {bool(os.getenv('DATABRICKS_CLIENT_ID'))}")
    print(f"   DATABRICKS_CLIENT_SECRET: {bool(os.getenv('DATABRICKS_CLIENT_SECRET'))}")
    print()

    # Determine storage type based on available credentials
    if os.getenv("DATABRICKS_HOST") and os.getenv("DATABRICKS_WAREHOUSE_ID"):
        print("🚀 Databricks environment detected. Attempting Delta Lake storage...")
        try:
            print("   → Creating DeltaStorage instance...")
            storage = DeltaStorage()
            print(f"   → DeltaStorage instance created: {storage}")
            print("   → Initializing connection...")
            await storage.initialize()
            print("✅ DeltaStorage initialized successfully")
        except Exception as e:
            print(f"❌ DeltaStorage initialization failed: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            print("   Falling back to file-based storage")
            storage = FileStorage()
            await storage.initialize()
    else:
        # Check for PostgreSQL credentials
        use_file_storage = not all(
            [
                os.getenv("DB_USER"),
                os.getenv("DB_HOST"),
                os.getenv("DB_NAME"),
                os.getenv("DB_PASSWORD"),
            ]
        )

        if use_file_storage:
            print(
                "📁 No Databricks or PostgreSQL credentials found, using file-based storage."
            )
            storage = FileStorage()
        else:
            print("🐘 PostgreSQL credentials found, using PostgreSQL database.")
            # TODO: Add PostgreSQL support if needed
            raise NotImplementedError("PostgreSQL support not yet implemented in Python backend")

        await storage.initialize()
    
    print(f"✅ Storage backend initialized: {storage.__class__.__name__}")
    print(f"   Storage instance type: {type(storage)}")
    print(f"   Storage is None: {storage is None}\n")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    try:
        if storage:
            # Try a simple operation to verify storage is working
            await storage.get_registrations(limit=1)
        return {"status": "healthy", "storage": "connected"}
    except Exception as e:
        return {"status": "unhealthy", "storage": "disconnected", "error": str(e)}


@app.post("/api/register")
async def register(req: RegistrationRequest):
    """Register a new attendee."""
    try:
        await initialize_storage()  # Ensure storage is initialized
    except Exception as e:
        print(f"❌ Storage initialization failed: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Storage initialization failed: {str(e)}",
        )
    
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
        # Check if email already exists
        exists = await storage.check_email_exists(email)
        if exists:
            if not ALLOW_EMAIL_REUSE:
                raise HTTPException(
                    status_code=409,
                    detail="You're already checked in with this email.",
                )
            # If reuse allowed, treat as successful (idempotent)
            return {"message": "Check-in successful! Welcome back."}

        # Save registration
        reg_id = await storage.save_registration(
            name=f"{first_name} {last_name}",
            email=email,
            company=company,
            group="General",  # TODO: Make this configurable
            location="General",  # TODO: Make this configurable
        )

        return {"message": "Check-in successful! Welcome."}

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Registration error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Registration failed: {str(e)}",
        )


@app.post("/api/print")
async def print_nametag(req: PrintRequest):
    """Generate and send name tag to printer."""
    try:
        await initialize_storage()  # Ensure storage is initialized
    except Exception as e:
        print(f"❌ Storage initialization failed for print: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Storage initialization failed: {str(e)}",
        )
    
    if not req.firstName:
        raise HTTPException(status_code=400, detail="First name is required for printing.")

    name_display = req.firstName.strip().upper()
    company_val = (req.company or "").strip()
    group = (req.groupName or "Energy & Utilities Data Connect").strip()
    location = (req.location or "Sydney").strip()

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

        # Generate filename with timestamp
        timestamp = datetime.now(timezone.utc).isoformat().replace(":", "-")[: -5]
        base_filename = f"nametag_{name_display.replace(' ', '_')}_{timestamp}"
        png_filename = f"{base_filename}.png"

        # TODO: Save image to storage (disabled for now, will implement later)
        # await storage.save_image_to_volume(image_buffer, png_filename)

        # If no printer server URL, return image for download only
        if not NIIMBOT_SERVER_URL:
            print(
                f"Print (no printer server): {group} / {location} - {name_display} ({company_val})"
            )
            return {
                "message": "Name tag image generated. Set NIIMBOT_SERVER_URL to send to printer.",
                "data": {
                    "groupName": group,
                    "location": location,
                    "name": name_display,
                    "company": company_val,
                },
            }

        # Convert image to base64 for sending to printer
        import base64

        image_base64 = base64.b64encode(image_buffer).decode()

        # Connect to printer if transport/address set
        transport = os.getenv("NIIMBOT_TRANSPORT")
        address = os.getenv("NIIMBOT_ADDRESS")

        if transport and address:
            async with httpx.AsyncClient() as client:
                connect_res = await client.post(
                    f"{NIIMBOT_SERVER_URL}/connect",
                    json={"transport": transport, "address": address},
                )
                if connect_res.status_code != 200:
                    raise HTTPException(
                        status_code=502,
                        detail="Could not connect to printer. Check NIIMBOT_TRANSPORT and NIIMBOT_ADDRESS.",
                    )

        # Send print job
        async with httpx.AsyncClient() as client:
            print_res = await client.post(
                f"{NIIMBOT_SERVER_URL}/print",
                json={
                    "imageBase64": image_base64,
                    "labelWidth": LABEL_WIDTH_PX,
                    "labelHeight": LABEL_HEIGHT_PX,
                    "printTask": os.getenv("NIIMBOT_PRINT_TASK", "B1"),
                    "printDirection": "top",
                    "quantity": 1,
                },
            )

            if print_res.status_code != 200:
                print(f"❌ Print failed: {print_res.text}")
                raise HTTPException(
                    status_code=502,
                    detail="Printer rejected the job. Check niimblue-node server and printer connection.",
                )

            result = print_res.json()

        return {
            "message": result.get("message", "Name tag sent to printer successfully."),
            "data": {
                "groupName": group,
                "location": location,
                "name": name_display,
                "company": company_val,
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
        await initialize_storage()  # Ensure storage is initialized
        registrations = await storage.get_registrations(limit=limit)
        return {"registrations": registrations, "count": len(registrations)}
    except Exception as e:
        print(f"❌ Failed to fetch registrations: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to fetch registrations: {str(e)}")


# Root endpoint - serve index.html
@app.get("/")
async def root():
    """Serve the HTML frontend."""
    html_file = os.path.join(src_dir, "public", "index.html")
    html_file_abs = os.path.abspath(html_file)
    print(f"🌐 Root request - looking for index.html at: {html_file_abs}")
    if os.path.exists(html_file_abs):
        print(f"✅ Found index.html, serving it")
        return FileResponse(html_file_abs, media_type="text/html")
    print(f"❌ index.html not found at: {html_file_abs}")
    return {"message": "Event Registration Backend API", "debug": {"looking_for": html_file_abs, "exists": False}}
