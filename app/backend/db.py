"""Simple Databricks database connection and operations."""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from databricks import sql
from typing import List, Dict, Any, Optional
from pathlib import Path

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("event_registration.backend.db")

# Global connection
_connection = None
_use_file_storage = False  # Flag to use JSON file when Databricks unavailable
_data_file = Path(__file__).parent / "data" / "registrations.json"


def _is_databricks_app() -> bool:
    """Return True when running inside a Databricks App deployment."""
    return bool(os.getenv("DATABRICKS_APP_NAME"))


def _is_delta_only_mode() -> bool:
    """Databricks App deployments must use Delta tables and never local JSON."""
    return _is_databricks_app()


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
        logger.info("Database connection already initialized")
        return
    
    host = os.getenv("DATABRICKS_HOST")
    warehouse_id = os.getenv("DATABRICKS_WAREHOUSE_ID")
    
    if not host or not warehouse_id:
        if _is_delta_only_mode():
            raise RuntimeError(
                "Delta table mode is required in Databricks App. "
                "Missing DATABRICKS_HOST or DATABRICKS_WAREHOUSE_ID."
            )
        logger.warning("Databricks credentials missing; using file storage fallback")
        _use_file_storage = True
        return
    
    try:
        logger.info("Connecting to Databricks | host=%s | warehouse_id_present=%s", host, bool(warehouse_id))
        
        kwargs = {
            "server_hostname": host,
            "http_path": f"/sql/1.0/warehouses/{warehouse_id}",
        }
        
        # Use OAuth M2M if available, otherwise default auth
        client_id = os.getenv("DATABRICKS_CLIENT_ID")
        client_secret = os.getenv("DATABRICKS_CLIENT_SECRET")
        
        if client_id and client_secret:
            logger.info("Using OAuth M2M authentication for Databricks SQL")
            kwargs["auth_type"] = "oauth-m2m"
            kwargs["client_id"] = client_id
            kwargs["client_secret"] = client_secret
        else:
            logger.info("Using default Databricks authentication")
        
        # Run connection in thread pool to avoid blocking and fail fast if auth is interactive.
        _connection = await asyncio.wait_for(asyncio.to_thread(sql.connect, **kwargs), timeout=12)
        logger.info("Connected to Databricks successfully")
        
    except Exception as e:
        if _is_delta_only_mode():
            logger.exception(
                "Databricks connection failed in Delta-only mode | error=%s",
                e,
            )
            raise RuntimeError(
                "Unable to connect to Databricks in Delta-only mode. "
                "JSON fallback is disabled when DATABRICKS_APP_NAME is set."
            ) from e
        logger.warning(
            "Databricks connection unavailable; falling back to file storage | error=%s",
            e,
        )
        _connection = None
        _use_file_storage = True
        return


async def execute_query(query: str, params: List[Any] = None) -> List[tuple]:
    """Execute a query and return rows."""
    if _connection is None:
        raise RuntimeError("Not connected to Databricks")

    normalized_query = " ".join(query.split())
    logger.debug("Executing query | sql=%s | has_params=%s", normalized_query[:240], bool(params))
    
    def _execute():
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
        if _connection is None:
            await init_connection()

        if _use_file_storage:
            # Use file storage
            registrations = await asyncio.to_thread(_load_registrations)
            logger.debug("Email exists check via file storage | records=%s", len(registrations))
            return any(r.get("company_email", "").lower() == email.lower() for r in registrations)
        
        rows = await execute_query(
            "SELECT 1 FROM main.default.event_registrations WHERE company_email = ? LIMIT 1",
            [email]
        )
        return len(rows) > 0
    except Exception as e:
        logger.exception("check_email_exists failed | error=%s", e)
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
        if _connection is None:
            await init_connection()

        if _use_file_storage:
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
            logger.info("Saved registration to file storage | id=%s", next_id)
            return next_id
        
        await execute_query(
            """
            INSERT INTO main.default.event_registrations 
            (first_name, last_name, company, company_email, contact_permission)
            VALUES (?, ?, ?, ?, ?)
            """,
            [first_name, last_name, company, email, contact_permission]
        )
        logger.info("Saved registration to Databricks table")
        return 1  # Success
    except Exception as e:
        logger.exception("save_registration failed | error=%s", e)
        raise


async def attach_nametag_filename(registration_id: int, nametag_filename: str) -> None:
    """Persist nametag image filename on a registration record."""
    try:
        if _connection is None:
            await init_connection()

        if _use_file_storage:
            registrations = await asyncio.to_thread(_load_registrations)
            updated = False

            for registration in registrations:
                if int(registration.get("id", 0)) == int(registration_id):
                    registration["nametag_image_filename"] = nametag_filename
                    updated = True
                    break

            if updated:
                await asyncio.to_thread(_save_registrations, registrations)
                logger.info("Attached nametag filename in file storage | registration_id=%s", registration_id)
            else:
                logger.warning("Registration not found for nametag attachment | registration_id=%s", registration_id)
            return

        await execute_query(
            """
            UPDATE main.default.event_registrations
            SET nametag_image_filename = ?
            WHERE id = ?
            """,
            [nametag_filename, registration_id],
        )
        logger.info("Attached nametag filename in Databricks | registration_id=%s", registration_id)
    except Exception as e:
        logger.exception("attach_nametag_filename failed | registration_id=%s | error=%s", registration_id, e)
        raise


async def get_registrations(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Get all registrations from Databricks or file storage."""
    try:
        if _connection is None:
            await init_connection()

        if _use_file_storage:
            # Use file storage
            registrations = await asyncio.to_thread(_load_registrations)
            # Sort by created_at descending
            registrations.sort(key=lambda r: r.get("created_at", 0), reverse=True)
            if limit:
                registrations = registrations[:limit]
            logger.info("Fetched registrations from file storage | count=%s | limit=%s", len(registrations), limit)
            return registrations
        
        query = "SELECT id, first_name, last_name, company, company_email, contact_permission, created_at FROM main.default.event_registrations ORDER BY created_at DESC"
        if limit:
            query += f" LIMIT {limit}"
        
        rows = await execute_query(query)
        
        # Convert tuples to dicts
        columns = ["id", "first_name", "last_name", "company", "company_email", "contact_permission", "created_at"]
        logger.info("Fetched registrations from Databricks | count=%s | limit=%s", len(rows), limit)
        return [dict(zip(columns, row)) for row in rows]
    except Exception as e:
        logger.exception("get_registrations failed | error=%s", e)
        raise


async def close_connection():
    """Close database connection."""
    global _connection
    if _connection:
        await asyncio.to_thread(_connection.close)
        _connection = None
        logger.info("Database connection closed")
