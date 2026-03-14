"""Simple Databricks database connection and operations."""

import asyncio
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from databricks import sql
from typing import List, Dict, Any, Optional
from pathlib import Path
import httpx

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("event_registration.backend.db")


class DatabricksConnectionError(RuntimeError):
    """Raised when Databricks connection is required but cannot be established."""

    def __init__(self, message: str, diagnostics: Dict[str, Any]):
        super().__init__(message)
        self.diagnostics = diagnostics

# Global connection
_connection = None
_use_file_storage = False  # Flag to use JSON file when Databricks unavailable
_data_file = Path(__file__).parent / "data" / "registrations.json"
REGISTRATIONS_TABLE = os.getenv("REGISTRATIONS_TABLE", "main.default.event_registrations").strip()
_REGISTRATION_ID_EPOCH_MS = 1735689600000  # 2025-01-01T00:00:00Z
_REGISTRATION_ID_WORKER_ID = uuid.uuid4().int & 0x3FF
_REGISTRATION_ID_LOCK = threading.Lock()
_REGISTRATION_ID_LAST_MS = -1
_REGISTRATION_ID_COUNTER = 0


def _is_databricks_app() -> bool:
    """Return True when running inside a Databricks App deployment."""
    return bool(os.getenv("DATABRICKS_APP_NAME"))


def _is_delta_only_mode() -> bool:
    """Databricks App deployments must use Delta tables and never local JSON."""
    return _is_databricks_app()


def _connection_diagnostics() -> Dict[str, Any]:
    """Return safe diagnostics for Databricks SQL connection troubleshooting."""
    host = os.getenv("DATABRICKS_HOST") or ""
    warehouse_id = os.getenv("DATABRICKS_WAREHOUSE_ID") or ""
    client_id = os.getenv("DATABRICKS_CLIENT_ID") or ""
    client_secret = os.getenv("DATABRICKS_CLIENT_SECRET") or ""
    token = os.getenv("DATABRICKS_TOKEN") or ""

    return {
        "databricks_app": _is_databricks_app(),
        "delta_only_mode": _is_delta_only_mode(),
        "host_configured": bool(host),
        "warehouse_id_configured": bool(warehouse_id),
        "auth_mode": "oauth-m2m" if client_id and client_secret else "default",
        "client_id_configured": bool(client_id),
        "client_secret_configured": bool(client_secret),
        "token_configured": bool(token),
    }


def _normalize_server_hostname(host: str) -> str:
    """Normalize host input to Databricks SQL connector server_hostname format."""
    normalized = (host or "").strip()
    normalized = re.sub(r"^https?://", "", normalized)
    normalized = normalized.split("/", 1)[0]
    return normalized


async def _fetch_oauth_m2m_access_token(server_hostname: str, client_id: str, client_secret: str) -> str:
    """Fetch access token via OAuth client credentials against workspace OIDC endpoint."""
    token_url = f"https://{server_hostname}/oidc/v1/token"
    payload = {
        "grant_type": "client_credentials",
        "scope": "all-apis",
        "client_id": client_id,
        "client_secret": client_secret,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0)) as client:
        response = await client.post(token_url, data=payload)
        response.raise_for_status()
        token_data = response.json() if response.content else {}
        access_token = token_data.get("access_token")
        if not access_token:
            raise RuntimeError("OAuth token response missing access_token")
        return str(access_token)


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


def _generate_registration_id() -> int:
    """Generate a unique 63-bit time-sequence bigint registration ID.

    Layout: 41 bits timestamp since 2025-01-01 UTC, 10 bits worker id,
    12 bits per-millisecond counter.
    """
    global _REGISTRATION_ID_LAST_MS, _REGISTRATION_ID_COUNTER

    with _REGISTRATION_ID_LOCK:
        current_ms = max(0, (time.time_ns() // 1_000_000) - _REGISTRATION_ID_EPOCH_MS)

        if current_ms < _REGISTRATION_ID_LAST_MS:
            current_ms = _REGISTRATION_ID_LAST_MS

        if current_ms == _REGISTRATION_ID_LAST_MS:
            _REGISTRATION_ID_COUNTER += 1
            if _REGISTRATION_ID_COUNTER > 0xFFF:
                while current_ms <= _REGISTRATION_ID_LAST_MS:
                    current_ms = max(0, (time.time_ns() // 1_000_000) - _REGISTRATION_ID_EPOCH_MS)
                _REGISTRATION_ID_COUNTER = 0
        else:
            _REGISTRATION_ID_COUNTER = 0

        _REGISTRATION_ID_LAST_MS = current_ms

        return (current_ms << 22) | (_REGISTRATION_ID_WORKER_ID << 12) | _REGISTRATION_ID_COUNTER


def generate_registration_id() -> int:
    """Expose registration ID generation for callers that need a shared ID."""
    return _generate_registration_id()


async def _build_connection_kwargs() -> Dict[str, Any]:
    """Build Databricks SQL connection kwargs for shared or isolated queries."""
    host = os.getenv("DATABRICKS_HOST")
    warehouse_id = os.getenv("DATABRICKS_WAREHOUSE_ID")
    if not host or not warehouse_id:
        raise RuntimeError("DATABRICKS_HOST and DATABRICKS_WAREHOUSE_ID are required")

    server_hostname = _normalize_server_hostname(host)
    if not server_hostname:
        raise RuntimeError("DATABRICKS_HOST could not be normalized to a valid server hostname")

    kwargs: Dict[str, Any] = {
        "server_hostname": server_hostname,
        "http_path": f"/sql/1.0/warehouses/{warehouse_id}",
    }

    client_id = os.getenv("DATABRICKS_CLIENT_ID")
    client_secret = os.getenv("DATABRICKS_CLIENT_SECRET")
    access_token = os.getenv("DATABRICKS_TOKEN")

    if not access_token and client_id and client_secret:
        logger.info("Fetching OAuth M2M access token for Databricks SQL")
        access_token = await _fetch_oauth_m2m_access_token(server_hostname, client_id, client_secret)

    if access_token:
        logger.info("Using access token authentication for Databricks SQL")
        kwargs["access_token"] = access_token
    else:
        logger.info("No explicit token configured; using default Databricks SQL authentication")

    if _is_delta_only_mode() and "access_token" not in kwargs:
        diagnostics = _connection_diagnostics()
        diagnostics["missing_auth"] = [
            "DATABRICKS_TOKEN or OAuth M2M credentials (DATABRICKS_CLIENT_ID + DATABRICKS_CLIENT_SECRET)"
        ]
        raise DatabricksConnectionError(
            "Delta-only mode requires non-interactive Databricks SQL authentication, but no usable token credentials were found.",
            diagnostics,
        )

    return kwargs


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
            diagnostics = _connection_diagnostics()
            diagnostics["missing_env"] = [
                env_name
                for env_name, present in {
                    "DATABRICKS_HOST": bool(host),
                    "DATABRICKS_WAREHOUSE_ID": bool(warehouse_id),
                }.items()
                if not present
            ]
            raise DatabricksConnectionError(
                "Delta table mode is required in Databricks App, but required Databricks SQL environment variables are missing.",
                diagnostics,
            )
        logger.warning("Databricks credentials missing; using file storage fallback")
        _use_file_storage = True
        return
    
    try:
        server_hostname = _normalize_server_hostname(host)
        if not server_hostname:
            raise RuntimeError("DATABRICKS_HOST could not be normalized to a valid server hostname")

        logger.info(
            "Connecting to Databricks | host=%s | warehouse_id_present=%s",
            server_hostname,
            bool(warehouse_id),
        )
        
        kwargs = await _build_connection_kwargs()
        
        # Run connection in thread pool to avoid blocking event loop; include retry for transient startup/network delays.
        connect_timeout_seconds = int(os.getenv("DATABRICKS_SQL_CONNECT_TIMEOUT_SECONDS", "30"))
        last_exc: Optional[Exception] = None

        for attempt in range(1, 3):
            try:
                _connection = await asyncio.wait_for(
                    asyncio.to_thread(sql.connect, **kwargs),
                    timeout=connect_timeout_seconds,
                )
                break
            except Exception as connect_exc:
                last_exc = connect_exc
                logger.warning(
                    "Databricks connection attempt failed | attempt=%s | timeout_seconds=%s | error_type=%s | error=%s",
                    attempt,
                    connect_timeout_seconds,
                    type(connect_exc).__name__,
                    connect_exc,
                )
                if attempt < 2:
                    await asyncio.sleep(2)

        if _connection is None and last_exc is not None:
            raise last_exc

        logger.info("Connected to Databricks successfully")
        
    except Exception as e:
        if _is_delta_only_mode():
            diagnostics = _connection_diagnostics()
            diagnostics["connect_error_type"] = type(e).__name__
            diagnostics["connect_error"] = str(e)
            diagnostics["connect_timeout_seconds"] = int(
                os.getenv("DATABRICKS_SQL_CONNECT_TIMEOUT_SECONDS", "30")
            )
            logger.exception(
                "Databricks connection failed in Delta-only mode | error=%s",
                e,
            )
            raise DatabricksConnectionError(
                "Unable to connect to Databricks in Delta-only mode. JSON fallback is disabled when DATABRICKS_APP_NAME is set.",
                diagnostics,
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
            try:
                return cursor.fetchall()
            except Exception:
                return []
        finally:
            cursor.close()
    
    return await asyncio.to_thread(_execute)


async def execute_query_isolated(query: str, params: List[Any] = None) -> List[tuple]:
    """Execute a query using a short-lived dedicated Databricks SQL connection."""
    if _use_file_storage:
        raise RuntimeError("Isolated Databricks queries are unavailable in file storage mode")

    kwargs = await _build_connection_kwargs()
    normalized_query = " ".join(query.split())
    logger.debug("Executing isolated query | sql=%s | has_params=%s", normalized_query[:240], bool(params))

    def _execute():
        connection = sql.connect(**kwargs)
        try:
            cursor = connection.cursor()
            try:
                if params:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)
                try:
                    return cursor.fetchall()
                except Exception:
                    return []
            finally:
                cursor.close()
        finally:
            connection.close()

    return await asyncio.to_thread(_execute)


async def save_registration(
    first_name: str, 
    last_name: str, 
    email: str, 
    company: str, 
    contact_permission: bool = False,
    registration_id: Optional[int] = None,
) -> int:
    """Save registration to Databricks or file storage."""
    try:
        if _connection is None:
            await init_connection()

        if _use_file_storage:
            # Use file storage
            registrations = await asyncio.to_thread(_load_registrations)

            next_id = registration_id if registration_id is not None else _generate_registration_id()

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
        
        registration_id = registration_id if registration_id is not None else _generate_registration_id()

        await execute_query(
            f"""
            INSERT INTO {REGISTRATIONS_TABLE}
            (id, first_name, last_name, company, company_email, contact_permission)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                registration_id,
                first_name,
                last_name,
                company,
                email,
                contact_permission,
            ],
        )
        logger.info("Saved registration to Databricks table | id=%s", registration_id)
        return registration_id
    except Exception as e:
        logger.exception("save_registration failed | error=%s", e)
        raise


async def delete_registration(registration_id: int) -> None:
    """Delete one registration record by ID for rollback scenarios."""
    try:
        if _connection is None:
            await init_connection()

        if _use_file_storage:
            registrations = await asyncio.to_thread(_load_registrations)
            filtered = [
                registration
                for registration in registrations
                if str(registration.get("id")) != str(registration_id)
            ]
            if len(filtered) != len(registrations):
                await asyncio.to_thread(_save_registrations, filtered)
                logger.info("Deleted registration from file storage | id=%s", registration_id)
            return

        await execute_query(
            f"DELETE FROM {REGISTRATIONS_TABLE} WHERE id = ?",
            [registration_id],
        )
        logger.info("Deleted registration from Databricks table | id=%s", registration_id)
    except Exception as e:
        logger.exception("delete_registration failed | id=%s | error=%s", registration_id, e)
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
        
        query = f"SELECT id, first_name, last_name, company, company_email, contact_permission, created_at FROM {REGISTRATIONS_TABLE} ORDER BY created_at DESC"
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
