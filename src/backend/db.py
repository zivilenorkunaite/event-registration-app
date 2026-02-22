"""Simple Databricks database connection and operations."""

import asyncio
import os
from databricks import sql
from typing import List, Dict, Any, Optional

# Global connection
_connection = None


async def init_connection():
    """Initialize Databricks connection once at startup."""
    global _connection
    
    if _connection is not None:
        print("✅ Connection already initialized")
        return
    
    host = os.getenv("DATABRICKS_HOST")
    warehouse_id = os.getenv("DATABRICKS_WAREHOUSE_ID")
    
    if not host or not warehouse_id:
        print("⚠️  No Databricks credentials, using file storage fallback")
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
    """Save registration to Databricks."""
    try:
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


async def get_registrations(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Get all registrations."""
    try:
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
