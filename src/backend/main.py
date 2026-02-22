"""FastAPI backend server for event registration and name tag printing."""

import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr
import httpx

from backend import db
from backend.services.nametag import generate_nametag_image, LABEL_WIDTH_PX, LABEL_HEIGHT_PX

print("✅ All imports successful")


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


# Global configuration
EMAIL_REGEX = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
ALLOW_EMAIL_REUSE = os.getenv("ALLOW_EMAIL_REUSE", "true").lower() != "false"
NIIMBOT_SERVER_URL = os.getenv("NIIMBOT_SERVER_URL", "").rstrip("/")


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

# Mount static files
backend_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.dirname(backend_dir)
public_dir = os.path.abspath(os.path.join(src_dir, "public"))

if os.path.isdir(public_dir):
    print(f"✅ Mounting static files from {public_dir}")
    app.mount("/static", StaticFiles(directory=public_dir), name="static")
else:
    print(f"⚠️  Public directory not found at {public_dir}")



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
        await db.save_registration(
            first_name=first_name,
            last_name=last_name,
            email=email,
            company=company,
            contact_permission=req.contactPermission,
        )

        return {"message": "Check-in successful! Welcome."}

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
    """Generate and send name tag to printer."""
    
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
        # await db.save_image_to_volume(image_buffer, png_filename)

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
                        detail="Could not connect to printer.",
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
                    detail="Printer rejected the job.",
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
        # Ensure connection is initialized
        if db._connection is None:
            await db.init_connection()
        
        registrations = await db.get_registrations(limit=limit)
        return {"registrations": registrations, "count": len(registrations)}
    except Exception as e:
        print(f"❌ Failed to fetch registrations: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch registrations: {str(e)}")


@app.get("/")
async def root():
    """Serve the HTML frontend."""
    html_file = os.path.join(src_dir, "public", "index.html")
    if os.path.exists(html_file):
        return FileResponse(html_file, media_type="text/html")
    return {"message": "Event Registration Backend API"}

