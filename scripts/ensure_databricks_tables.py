#!/usr/bin/env python3
"""Ensure Databricks registration and queue tables exist with required columns."""

from __future__ import annotations

import asyncio
import os
import re
import sys
from typing import Any

import httpx
from databricks import sql


def _normalize_server_hostname(host: str) -> str:
    normalized = (host or "").strip()
    normalized = re.sub(r"^https?://", "", normalized)
    normalized = normalized.split("/", 1)[0]
    return normalized


async def _fetch_oauth_m2m_access_token(server_hostname: str, client_id: str, client_secret: str) -> str:
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


async def _build_connection_kwargs() -> dict[str, Any]:
    host = os.getenv("DATABRICKS_HOST", "").strip()
    warehouse_id = os.getenv("DATABRICKS_WAREHOUSE_ID", "").strip()
    client_id = os.getenv("DATABRICKS_CLIENT_ID", "").strip()
    client_secret = os.getenv("DATABRICKS_CLIENT_SECRET", "").strip()
    token = os.getenv("DATABRICKS_TOKEN", "").strip()

    if not host or not warehouse_id:
        raise RuntimeError("DATABRICKS_HOST and DATABRICKS_WAREHOUSE_ID are required")

    server_hostname = _normalize_server_hostname(host)
    if not server_hostname:
        raise RuntimeError("DATABRICKS_HOST could not be normalized")

    kwargs: dict[str, Any] = {
        "server_hostname": server_hostname,
        "http_path": f"/sql/1.0/warehouses/{warehouse_id}",
    }

    if not token and client_id and client_secret:
        token = await _fetch_oauth_m2m_access_token(server_hostname, client_id, client_secret)

    if token:
        kwargs["access_token"] = token
    else:
        raise RuntimeError(
            "Set DATABRICKS_TOKEN or both DATABRICKS_CLIENT_ID and DATABRICKS_CLIENT_SECRET"
        )

    return kwargs


def _execute(connection: Any, statement: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
    cursor = connection.cursor()
    try:
        if params:
            cursor.execute(statement, params)
        else:
            cursor.execute(statement)
        try:
            return cursor.fetchall()
        except Exception:
            return []
    finally:
        cursor.close()


def _describe_columns(connection: Any, table_name: str) -> set[str]:
    rows = _execute(connection, f"DESCRIBE TABLE {table_name}")
    columns: set[str] = set()
    for row in rows:
        column_name = str((row[0] if len(row) > 0 else "") or "").strip()
        column_type = str((row[1] if len(row) > 1 else "") or "").strip()
        if not column_name or column_name.startswith("#"):
            continue
        if not column_type:
            continue
        columns.add(column_name.lower())
    return columns


def _ensure_columns(connection: Any, table_name: str, required_columns: dict[str, str]) -> None:
    existing_columns = _describe_columns(connection, table_name)
    missing_columns = [
        f"{column_name} {column_type}"
        for column_name, column_type in required_columns.items()
        if column_name.lower() not in existing_columns
    ]
    if not missing_columns:
        return
    _execute(connection, f"ALTER TABLE {table_name} ADD COLUMNS ({', '.join(missing_columns)})")


async def main() -> int:
    catalog = os.getenv("DATABRICKS_CATALOG", "main").strip()
    schema = os.getenv("DATABRICKS_SCHEMA", "default").strip()
    registrations_table = os.getenv(
        "REGISTRATIONS_TABLE",
        f"{catalog}.{schema}.event_registrations",
    ).strip()
    queue_table = os.getenv("LOCAL_AGENT_QUEUE_TABLE", "").strip()

    if not re.match(r"^[A-Za-z0-9_]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+$", registrations_table):
        raise RuntimeError(f"Invalid REGISTRATIONS_TABLE: {registrations_table}")
    if not re.match(r"^[A-Za-z0-9_]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+$", queue_table):
        raise RuntimeError(f"Invalid LOCAL_AGENT_QUEUE_TABLE: {queue_table}")

    kwargs = await _build_connection_kwargs()
    connection = sql.connect(**kwargs)
    try:
        _execute(connection, f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")

        _execute(
            connection,
            f"""
            CREATE TABLE IF NOT EXISTS {registrations_table}
            (
                id BIGINT,
                first_name STRING NOT NULL,
                last_name STRING NOT NULL,
                company STRING,
                company_email STRING NOT NULL,
                contact_permission BOOLEAN,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
            )
            USING DELTA
            """,
        )
        _ensure_columns(
            connection,
            registrations_table,
            {
                "id": "BIGINT",
                "first_name": "STRING",
                "last_name": "STRING",
                "company": "STRING",
                "company_email": "STRING",
                "contact_permission": "BOOLEAN",
                "created_at": "TIMESTAMP",
            },
        )

        _execute(
            connection,
            f"""
            CREATE TABLE IF NOT EXISTS {queue_table}
            (
                job_id STRING,
                status STRING,
                payload_json STRING,
                created_at TIMESTAMP,
                updated_at TIMESTAMP,
                attempt_count INT,
                max_attempts INT,
                next_attempt_at TIMESTAMP,
                printer_id STRING,
                claimed_by STRING,
                claimed_at TIMESTAMP,
                claim_expires_at TIMESTAMP,
                printed_at TIMESTAMP,
                error_message STRING
            )
            USING DELTA
            """,
        )
        _ensure_columns(
            connection,
            queue_table,
            {
                "job_id": "STRING",
                "status": "STRING",
                "payload_json": "STRING",
                "created_at": "TIMESTAMP",
                "updated_at": "TIMESTAMP",
                "attempt_count": "INT",
                "max_attempts": "INT",
                "next_attempt_at": "TIMESTAMP",
                "printer_id": "STRING",
                "claimed_by": "STRING",
                "claimed_at": "TIMESTAMP",
                "claim_expires_at": "TIMESTAMP",
                "printed_at": "TIMESTAMP",
                "error_message": "STRING",
            },
        )

        print(f"Ensured Databricks tables: {registrations_table}, {queue_table}")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:
        print(f"Failed to ensure Databricks tables: {exc}", file=sys.stderr)
        raise
