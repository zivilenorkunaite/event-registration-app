"""Databricks Delta Lake storage implementation."""

import os
from typing import Any, List, Dict, Optional
from databricks import sql
import aiohttp
from .base import StorageBase


class DeltaStorage(StorageBase):
    """Storage implementation using Databricks Delta Lake and UC Volumes."""

    def __init__(self):
        """Initialize Databricks storage configuration."""
        print("   [DeltaStorage] Reading environment variables...")
        self.host = os.getenv("DATABRICKS_HOST")
        self.warehouse_id = os.getenv("DATABRICKS_WAREHOUSE_ID")
        self.client_id = os.getenv("DATABRICKS_CLIENT_ID")
        self.client_secret = os.getenv("DATABRICKS_CLIENT_SECRET")
        self.volume_path = os.getenv("DATABRICKS_VOLUME_PATH", "/Volumes/main/default/name_tags")
        self.catalog = os.getenv("DATABRICKS_CATALOG", "main")
        self.schema = os.getenv("DATABRICKS_SCHEMA", "default")
        
        print(f"   [DeltaStorage] Host: {self.host}")
        print(f"   [DeltaStorage] Warehouse: {self.warehouse_id}")
        print(f"   [DeltaStorage] Catalog: {self.catalog}")
        print(f"   [DeltaStorage] Schema: {self.schema}")
        print(f"   [DeltaStorage] Volume: {self.volume_path}")

        if not self.host or not self.warehouse_id:
            raise ValueError(
                f"Missing Databricks configuration: DATABRICKS_HOST={'missing' if not self.host else 'set'}, DATABRICKS_WAREHOUSE_ID={'missing' if not self.warehouse_id else 'set'}"
            )

        self.connection = None
        self.http_path = f"/sql/1.0/warehouses/{self.warehouse_id}"

    async def initialize(self) -> None:
        """Connect to Databricks warehouse."""
        try:
            print("   [DeltaStorage] Attempting database connection...")
            # Try to connect using OAuth M2M if credentials are available
            if self.client_id and self.client_secret:
                print("   [DeltaStorage] Using OAuth M2M authentication")
                self.connection = sql.connect(
                    server_hostname=self.host,
                    http_path=self.http_path,
                    auth_type="oauth-m2m",
                    client_id=self.client_id,
                    client_secret=self.client_secret,
                )
            else:
                # Fall back to default authentication (works in Databricks Apps)
                print("   [DeltaStorage] Using default Databricks authentication")
                self.connection = sql.connect(
                    server_hostname=self.host,
                    http_path=self.http_path,
                )
            print("   [DeltaStorage] ✅ Connected to Databricks warehouse successfully")
        except Exception as e:
            print(f"   [DeltaStorage] ❌ Connection failed: {type(e).__name__}: {e}")
            raise

    async def query(self, sql_query: str, values: Optional[List[Any]] = None) -> Dict[str, Any]:
        """Execute a query against Databricks."""
        if not self.connection:
            raise RuntimeError("Storage not initialized. Call initialize() first.")

        try:
            cursor = self.connection.cursor()
            if values:
                cursor.execute(sql_query, values)
            else:
                cursor.execute(sql_query)

            rows = cursor.fetchall()
            return {"rows": rows if rows else []}
        except Exception as e:
            print(f"❌ Databricks query failed: {e}")
            raise

    async def check_email_exists(self, email: str) -> bool:
        """Check if email exists in registrations."""
        result = await self.query(
            f"""
            SELECT COUNT(*) as count FROM {self.catalog}.{self.schema}.event_registrations
            WHERE email = ?
            """,
            [email],
        )
        return result["rows"][0][0] > 0 if result["rows"] else False

    async def save_registration(
        self, name: str, email: str, company: str, group: str, location: str
    ) -> int:
        """Save a new registration to Delta Lake."""
        try:
            cursor = self.connection.cursor()
            cursor.execute(
                f"""
                INSERT INTO {self.catalog}.{self.schema}.event_registrations
                (name, email, company, group_name, location)
                VALUES (?, ?, ?, ?, ?)
                """,
                [name, email, company, group, location],
            )
            # Get the ID of the newly inserted row
            cursor.execute("SELECT LAST_INSERT_ID()")
            result = cursor.fetchone()
            return result[0] if result else 0
        except Exception as e:
            print(f"❌ Failed to save registration: {e}")
            raise

    async def get_registrations(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get all registrations from Delta Lake."""
        query = f"""
            SELECT id, name, email, company, group_name, location, created_at
            FROM {self.catalog}.{self.schema}.event_registrations
            ORDER BY created_at DESC
        """
        if limit:
            query += f" LIMIT {limit}"

        result = await self.query(query)
        return result["rows"]

    async def save_image_to_volume(self, image_buffer: bytes, filename: str) -> str:
        """Save PNG image to UC Volume using Databricks Files API."""
        if not self.host:
            print("⚠️ Databricks host not available. Cannot upload to UC volume.")
            return f"{self.volume_path}/{filename}"

        try:
            # Get OAuth access token
            access_token = await self._get_oauth_token()

            file_path = f"{self.volume_path}/{filename}"
            encoded_path = file_path.replace(" ", "%20")

            # Upload to UC volume via Databricks Files API
            async with aiohttp.ClientSession() as session:
                async with session.put(
                    f"https://{self.host}/api/2.0/fs/files/{encoded_path}",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/octet-stream",
                    },
                    data=image_buffer,
                ) as response:
                    if response.status != 200:
                        error_data = await response.json()
                        raise Exception(f"Files API error: {error_data.get('message', response.reason)}")

            print(f"✅ Saved name tag PNG to UC Volume: {filename}")
            return file_path
        except Exception as e:
            print(f"❌ Failed to save image to UC Volume: {e}")
            raise

    async def _get_oauth_token(self) -> str:
        """Get OAuth access token using client credentials flow."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"https://{self.host}/oidc/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "scope": "all-apis",
                    },
                ) as response:
                    if response.status != 200:
                        raise Exception(f"OAuth token request failed: {response.reason}")
                    data = await response.json()
                    return data["access_token"]
        except Exception as e:
            print(f"❌ Failed to get OAuth token: {e}")
            raise

    async def close(self) -> None:
        """Close Databricks connection."""
        if self.connection:
            self.connection.close()
            print("✅ Databricks connection closed")
