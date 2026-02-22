"""Abstract base class for storage implementations."""

from abc import ABC, abstractmethod
from typing import Any, List, Dict, Optional


class StorageBase(ABC):
    """Abstract base class defining the storage interface."""

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize storage connection (e.g., connect to database)."""
        pass

    @abstractmethod
    async def query(self, sql: str, values: Optional[List[Any]] = None) -> Dict[str, Any]:
        """
        Execute a query and return results.

        Args:
            sql: SQL query with placeholders (? for parameters)
            values: List of parameter values

        Returns:
            Dict with 'rows' key containing list of result rows
        """
        pass

    @abstractmethod
    async def check_email_exists(self, email: str) -> bool:
        """Check if email already exists in registrations."""
        pass

    @abstractmethod
    async def save_registration(
        self, name: str, email: str, company: str, group: str, location: str
    ) -> int:
        """
        Save a new registration to storage.

        Returns:
            ID of the newly created registration
        """
        pass

    @abstractmethod
    async def get_registrations(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get all registrations."""
        pass

    @abstractmethod
    async def save_image_to_volume(self, image_buffer: bytes, filename: str) -> str:
        """
        Save PNG image to storage (UC volume or local file).

        Args:
            image_buffer: PNG bytes
            filename: Filename for the image

        Returns:
            Path where image was saved
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """Cleanup storage connection."""
        pass
