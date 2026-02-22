"""File-based storage implementation for local development."""

import json
import os
from pathlib import Path
from typing import Any, List, Dict, Optional
from .base import StorageBase


class FileStorage(StorageBase):
    """File-based storage using JSON files."""

    def __init__(self):
        """Initialize file storage configuration."""
        self.data_dir = Path(__file__).parent.parent.parent / "data"
        self.registrations_file = self.data_dir / "registrations.json"
        self.images_dir = self.data_dir / "images"

    async def initialize(self) -> None:
        """Ensure data directories exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        print("✅ File storage initialized")

    async def query(self, sql_query: str, values: Optional[List[Any]] = None) -> Dict[str, Any]:
        """Not implemented for file storage."""
        raise NotImplementedError("File storage does not support raw SQL queries")

    async def check_email_exists(self, email: str) -> bool:
        """Check if email exists in registrations."""
        registrations = await self._load_registrations()
        return any(reg.get("email") == email for reg in registrations)

    async def save_registration(
        self, name: str, email: str, company: str, group: str, location: str
    ) -> int:
        """Save registration to JSON file."""
        registrations = await self._load_registrations()

        # Generate ID
        reg_id = max((int(reg.get("id", 0)) for reg in registrations), default=0) + 1

        new_registration = {
            "id": reg_id,
            "name": name,
            "email": email,
            "company": company,
            "group": group,
            "location": location,
            "created_at": str(
                __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            ),
        }

        registrations.append(new_registration)
        await self._save_registrations(registrations)

        return reg_id

    async def get_registrations(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get all registrations from JSON file."""
        registrations = await self._load_registrations()
        registrations.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        if limit:
            registrations = registrations[:limit]
        return registrations

    async def save_image_to_volume(self, image_buffer: bytes, filename: str) -> str:
        """Save PNG image to local file."""
        file_path = self.images_dir / filename
        file_path.write_bytes(image_buffer)
        print(f"✅ Saved name tag PNG to: {file_path}")
        return str(file_path)

    async def close(self) -> None:
        """Cleanup (no-op for file storage)."""
        pass

    async def _load_registrations(self) -> List[Dict[str, Any]]:
        """Load registrations from JSON file."""
        if not self.registrations_file.exists():
            return []
        with open(self.registrations_file, "r") as f:
            return json.load(f)

    async def _save_registrations(self, registrations: List[Dict[str, Any]]) -> None:
        """Save registrations to JSON file."""
        self.registrations_file.write_text(json.dumps(registrations, indent=2))
