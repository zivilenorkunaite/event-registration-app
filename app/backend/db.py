"""Simple Databricks database connection and operations."""

import asyncio
import json
import os
from datetime import datetime, timezone
from databricks import sql
from typing import List, Dict, Any, Optional
from pathlib import Path

# Global connection
_connection = None
_use_file_storage = False  # Flag to use JSON file when Databricks unavailable
_data_file = Path(__file__).parent / "data" / "registrations.json"


def _load_registrations() -> List[Dict[str, Any]]:
    """Load registrations from JSON file."""
    if _data_file.exists():
        with open(_data_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    return []


def _save_registrations(registrations: List[Dict[str, Any]]):
    """Save registrations to JSON file."""
    _data_file.parent.mkdir(parents=True, exist_ok=True)
    with open(_data_file, "w", encoding="utf-8") as f:
        json.dump(registrations, f, indent=2, default=str)


async def init_connection():
    """Initialize Databricks connection once at startup."""
    global _connection, _use_file_storage
    
    if _connection is not None:
        print("✅ Connection already initialized")
        return
    
    host = os.getenv("DATABRICKS_HOST")
    warehouse_id = os.getenv("DATABRICKS_WAREHOUSE_ID")
    
    if not host or not warehouse_id:
        print("⚠️  No Databricks credentials, using file storage fallback")
        _use_file_storage = True
        return
    
    try:
        print(f"🔌 Connecting to Databricks: {host}")
        
        kwargs = {
            "server_hostname": host,
            "http_path": f"/sql/1.0/warehouses/{warehouse_id}",
        }
        
        # Use OAuth M2M if available, otherwise default auth
        client_id = os.getenv("DATABRICKS_CLIENT_ID")
        client_secret = os.getenv("DATABRICKS_CLIENT_SECRET")
        
        if client_id and client_secret:
            print("   Using OAuth M2M authentication")
            kwargs["auth_type"] = "oauth-m2m"
            kwargs["client_id"] = client_id
            kwargs["client_secret"] = client_secret
        else:
            print("   Using default Databricks authentication")
        
        # Run connection in thread pool to avoid blocking
        _connection = await asyncio.to_thread(sql.connect, **kwargs)
        print("✅ Connected to Databricks successfully")
        
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        raise


async def execute_query(query: str, params: List[Any] = None) -> List[tuple]:
    """Execute a query and return rows."""
    if _connection is None:
        raise RuntimeError("Not connected to Databricks")
    
    async def _execute():
        cursor = _connection.cursor()
        try:
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            return cursor.fetchall()
        finally:
            cursor.close()
    
    return await asyncio.to_thread(_execute)


async def check_email_exists(email: str) -> bool:
    """Check if email exists in registrations table."""
    try:
        if _use_file_storage or _connection is None:
            # Use file storage
            registrations = await asyncio.to_thread(_load_registrations)
            return any(r.get("company_email", "").lower() == email.lower() for r in registrations)
        
        rows = await execute_query(
            "SELECT 1 FROM main.default.event_registrations WHERE company_email = ? LIMIT 1",
            [email]
        )
        return len(rows) > 0
    except Exception as e:
        print(f"❌ check_email_exists failed: {e}")
        raise


async def save_registration(
    first_name: str, 
    last_name: str, 
    email: str, 
    company: str, 
    contact_permission: bool = False
) -> int:
    """Save registration to Databricks or file storage."""
    try:
        if _use_file_storage or _connection is None:
            # Use file storage
            registrations = await asyncio.to_thread(_load_registrations)

            next_id = max(
                (int(reg.get("id", 0)) for reg in registrations if str(reg.get("id", "")).isdigit()),
                default=0,
            ) + 1

            registrations.append({
                "id": next_id,
                "first_name": first_name,
                "last_name": last_name,
                "company": company,
                "company_email": email,
                "contact_permission": contact_permission,
                "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            })
            await asyncio.to_thread(_save_registrations, registrations)
            return next_id
        
        await execute_query(
            """
            INSERT INTO main.default.event_registrations 
            (first_name, last_name, company, company_email, contact_permission)
            VALUES (?, ?, ?, ?, ?)
            """,
            [first_name, last_name, company, email, contact_permission]
        )
        return 1  # Success
    except Exception as e:
        print(f"❌ save_registration failed: {e}")
        raise


async def attach_nametag_filename(registration_id: int, nametag_filename: str) -> None:
    """Persist nametag image filename on a registration record."""
    try:
        if _use_file_storage or _connection is None:
            registrations = await asyncio.to_thread(_load_registrations)
            updated = False

            for registration in registrations:
                if int(registration.get("id", 0)) == int(registration_id):
                    registration["nametag_image_filename"] = nametag_filename
                    updated = True
                    break

            if updated:
                await asyncio.to_thread(_save_registrations, registrations)
            else:
                print(f"⚠️ Registration id {registration_id} not found for nametag attachment")
            return

        await execute_query(
            """
            UPDATE main.default.event_registrations
            SET nametag_image_filename = ?
            WHERE id = ?
            """,
            [nametag_filename, registration_id],
        )
    except Exception as e:
        print(f"❌ attach_nametag_filename failed: {e}")
        raise


async def get_registrations(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Get all registrations from Databricks or file storage."""
    try:
        if _use_file_storage or _connection is None:
            # Use file storage
            registrations = await asyncio.to_thread(_load_registrations)
            # Sort by created_at descending
            registrations.sort(key=lambda r: r.get("created_at", 0), reverse=True)
            if limit:
                registrations = registrations[:limit]
            return registrations
        
        query = "SELECT id, first_name, last_name, company, company_email, contact_permission, created_at FROM main.default.event_registrations ORDER BY created_at DESC"
        if limit:
            query += f" LIMIT {limit}"
        
        rows = await execute_query(query)
        
        # Convert tuples to dicts
        columns = ["id", "first_name", "last_name", "company", "company_email", "contact_permission", "created_at"]
        return [dict(zip(columns, row)) for row in rows]
    except Exception as e:
        print(f"❌ get_registrations failed: {e}")
        raise


async def close_connection():
    """Close database connection."""
    global _connection
    if _connection:
        await asyncio.to_thread(_connection.close)
        _connection = None
        print("✅ Database connection closed")
