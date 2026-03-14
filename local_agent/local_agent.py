"""Local print agent for Databricks-hosted queue jobs.

This worker is designed to run near the physical printer (laptop/mini-PC/RPi).
It polls a Databricks SQL table for queued print jobs, claims one safely,
sends it to a local Niimbot print bridge, and updates job status.
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import os
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from databricks import sql
from dotenv import load_dotenv


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class AgentConfig:
    host: str
    warehouse_id: str
    client_id: str
    client_secret: str
    token: str

    queue_table: str
    local_run: bool
    local_queue_file: str
    printer_id: str
    agent_id: str

    poll_seconds: int
    claim_ttl_seconds: int
    max_retry_backoff_seconds: int
    default_max_attempts: int

    niimbot_server_url: str
    niimbot_transport: str
    niimbot_address: str
    niimbot_print_task: str
    niimbot_print_direction: str
    niimbot_default_quantity: int
    label_width_px: int
    label_height_px: int
    auto_connect: bool


@dataclass
class ClaimedJob:
    job_id: str
    payload_json: str
    attempt_count: int
    max_attempts: int


def load_config() -> AgentConfig:
    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env")

    host = os.getenv("DATABRICKS_HOST", "").strip()
    warehouse_id = os.getenv("DATABRICKS_WAREHOUSE_ID", "").strip()
    client_id = os.getenv("DATABRICKS_CLIENT_ID", "").strip()
    client_secret = os.getenv("DATABRICKS_CLIENT_SECRET", "").strip()
    token = os.getenv("DATABRICKS_TOKEN", "").strip()
    local_run = _env_bool("LOCAL_AGENT_LOCAL_RUN", False)
    local_queue_file = os.getenv(
        "LOCAL_AGENT_LOCAL_QUEUE_FILE",
        "app/backend/data/print_jobs.json",
    ).strip()

    if not local_run:
        if not host or not warehouse_id:
            raise RuntimeError("DATABRICKS_HOST and DATABRICKS_WAREHOUSE_ID are required")
        if not token and not (client_id and client_secret):
            raise RuntimeError(
                "Set DATABRICKS_TOKEN OR both DATABRICKS_CLIENT_ID and DATABRICKS_CLIENT_SECRET"
            )

    default_agent_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"

    return AgentConfig(
        host=host,
        warehouse_id=warehouse_id,
        client_id=client_id,
        client_secret=client_secret,
        token=token,
        queue_table=os.getenv("LOCAL_AGENT_QUEUE_TABLE", "main.default.print_jobs").strip(),
        local_run=local_run,
        local_queue_file=local_queue_file,
        printer_id=os.getenv("LOCAL_AGENT_PRINTER_ID", "").strip(),
        agent_id=os.getenv("LOCAL_AGENT_ID", default_agent_id).strip(),
        poll_seconds=max(1, _env_int("LOCAL_AGENT_POLL_SECONDS", 2)),
        claim_ttl_seconds=max(10, _env_int("LOCAL_AGENT_CLAIM_TTL_SECONDS", 30)),
        max_retry_backoff_seconds=max(
            10, _env_int("LOCAL_AGENT_MAX_RETRY_BACKOFF_SECONDS", 120)
        ),
        default_max_attempts=max(1, _env_int("LOCAL_AGENT_DEFAULT_MAX_ATTEMPTS", 5)),
        niimbot_server_url=os.getenv("NIIMBOT_SERVER_URL", "http://localhost:5050").rstrip("/"),
        niimbot_transport=os.getenv("NIIMBOT_TRANSPORT", "ble").strip(),
        niimbot_address=os.getenv("NIIMBOT_ADDRESS", "").strip(),
        niimbot_print_task=os.getenv("NIIMBOT_PRINT_TASK", "B1").strip(),
        niimbot_print_direction=os.getenv("NIIMBOT_PRINT_DIRECTION", "top").strip(),
        niimbot_default_quantity=max(1, _env_int("NIIMBOT_DEFAULT_QUANTITY", 1)),
        label_width_px=max(8, _env_int("NIIMBOT_LABEL_WIDTH_PX", 560)),
        label_height_px=max(8, _env_int("NIIMBOT_LABEL_HEIGHT_PX", 320)),
        auto_connect=_env_bool("LOCAL_AGENT_AUTO_CONNECT", True),
    )


def connect_databricks(cfg: AgentConfig):
    if cfg.local_run:
        return contextlib.nullcontext(None)

    kwargs: dict[str, Any] = {
        "server_hostname": cfg.host,
        "http_path": f"/sql/1.0/warehouses/{cfg.warehouse_id}",
    }

    if cfg.token:
        kwargs["access_token"] = cfg.token
    else:
        kwargs["auth_type"] = "oauth-m2m"
        kwargs["client_id"] = cfg.client_id
        kwargs["client_secret"] = cfg.client_secret

    return sql.connect(**kwargs)


def connect_http_client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0))


def fetch_one(cursor, query: str, params: list[Any]) -> Optional[tuple[Any, ...]]:
    cursor.execute(query, params)
    row = cursor.fetchone()
    return row


def _queue_file_path(cfg: AgentConfig) -> Path:
    candidate = Path(cfg.local_queue_file)
    if candidate.is_absolute():
        return candidate
    repo_root = Path(__file__).resolve().parents[1]
    return (repo_root / candidate).resolve()


def _load_local_queue(cfg: AgentConfig) -> list[dict[str, Any]]:
    queue_file = _queue_file_path(cfg)
    if not queue_file.exists():
        return []

    with queue_file.open("r", encoding="utf-8") as file_handle:
        data = json.load(file_handle)
        if isinstance(data, list):
            return data
    return []


def _save_local_queue(cfg: AgentConfig, jobs: list[dict[str, Any]]) -> None:
    queue_file = _queue_file_path(cfg)
    queue_file.parent.mkdir(parents=True, exist_ok=True)
    with queue_file.open("w", encoding="utf-8") as file_handle:
        json.dump(jobs, file_handle, indent=2, ensure_ascii=False)


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _to_iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _eligible_local_job(job: dict[str, Any], cfg: AgentConfig, now: datetime) -> bool:
    if str(job.get("status") or "") != "queued":
        return False

    if cfg.printer_id:
        printer_id = job.get("printer_id")
        if printer_id not in (None, "", cfg.printer_id):
            return False

    next_attempt_at = _parse_iso_datetime(job.get("next_attempt_at"))
    if next_attempt_at and next_attempt_at > now:
        return False

    claim_expires_at = _parse_iso_datetime(job.get("claim_expires_at"))
    if claim_expires_at and claim_expires_at > now:
        return False

    return True


def _local_sort_key(job: dict[str, Any], key_name: str) -> str:
    value = job.get(key_name)
    if value is None:
        return ""
    return str(value)


def claim_next_job(conn, cfg: AgentConfig) -> Optional[ClaimedJob]:
    if cfg.local_run:
        jobs = _load_local_queue(cfg)
        now = datetime.now(timezone.utc)
        eligible = [job for job in jobs if _eligible_local_job(job, cfg, now)]
        if not eligible:
            return None

        eligible.sort(key=lambda item: _local_sort_key(item, "created_at"))
        candidate = eligible[0]
        job_id = str(candidate.get("job_id") or "")
        if not job_id:
            return None

        for job in jobs:
            if str(job.get("job_id") or "") != job_id:
                continue
            if str(job.get("status") or "") != "queued":
                return None

            job["status"] = "claimed"
            job["claimed_by"] = cfg.agent_id
            job["claimed_at"] = _to_iso_z(now)
            job["claim_expires_at"] = _to_iso_z(now + timedelta(seconds=cfg.claim_ttl_seconds))
            job["updated_at"] = _to_iso_z(now)
            _save_local_queue(cfg, jobs)

            return ClaimedJob(
                job_id=job_id,
                payload_json=str(job.get("payload_json") or "{}"),
                attempt_count=int(job.get("attempt_count") or 0),
                max_attempts=int(job.get("max_attempts") or cfg.default_max_attempts),
            )
        return None

    with conn.cursor() as cursor:
        if cfg.printer_id:
            select_sql = f"""
                SELECT job_id, payload_json, COALESCE(attempt_count, 0), COALESCE(max_attempts, ?)
                FROM {cfg.queue_table}
                WHERE status = 'queued'
                  AND (printer_id = ? OR printer_id IS NULL)
                  AND (next_attempt_at IS NULL OR next_attempt_at <= current_timestamp())
                  AND (claim_expires_at IS NULL OR claim_expires_at <= current_timestamp())
                ORDER BY created_at ASC
                LIMIT 1
            """
            candidate = fetch_one(
                cursor, select_sql, [cfg.default_max_attempts, cfg.printer_id]
            )
        else:
            select_sql = f"""
                SELECT job_id, payload_json, COALESCE(attempt_count, 0), COALESCE(max_attempts, ?)
                FROM {cfg.queue_table}
                WHERE status = 'queued'
                  AND (next_attempt_at IS NULL OR next_attempt_at <= current_timestamp())
                  AND (claim_expires_at IS NULL OR claim_expires_at <= current_timestamp())
                ORDER BY created_at ASC
                LIMIT 1
            """
            candidate = fetch_one(cursor, select_sql, [cfg.default_max_attempts])

        if not candidate:
            return None

        job_id = str(candidate[0])

        claim_sql = f"""
            UPDATE {cfg.queue_table}
            SET status = 'claimed',
                claimed_by = ?,
                claimed_at = current_timestamp(),
                claim_expires_at = timestampadd(SECOND, ?, current_timestamp()),
                updated_at = current_timestamp()
            WHERE job_id = ?
              AND status = 'queued'
              AND (claim_expires_at IS NULL OR claim_expires_at <= current_timestamp())
        """
        cursor.execute(claim_sql, [cfg.agent_id, cfg.claim_ttl_seconds, job_id])

        verify_sql = f"""
            SELECT job_id, payload_json, COALESCE(attempt_count, 0), COALESCE(max_attempts, ?)
            FROM {cfg.queue_table}
            WHERE job_id = ?
              AND status = 'claimed'
              AND claimed_by = ?
            LIMIT 1
        """
        claimed = fetch_one(
            cursor, verify_sql, [cfg.default_max_attempts, job_id, cfg.agent_id]
        )

        if not claimed:
            return None

        return ClaimedJob(
            job_id=str(claimed[0]),
            payload_json=str(claimed[1] or "{}"),
            attempt_count=int(claimed[2] or 0),
            max_attempts=int(claimed[3] or cfg.default_max_attempts),
        )


def count_queued_jobs(conn, cfg: AgentConfig) -> int:
    if cfg.local_run:
        jobs = _load_local_queue(cfg)
        now = datetime.now(timezone.utc)
        return sum(1 for job in jobs if _eligible_local_job(job, cfg, now))

    with conn.cursor() as cursor:
        if cfg.printer_id:
            sql_text = f"""
                SELECT COUNT(*)
                FROM {cfg.queue_table}
                WHERE status = 'queued'
                  AND (printer_id = ? OR printer_id IS NULL)
                  AND (next_attempt_at IS NULL OR next_attempt_at <= current_timestamp())
            """
            cursor.execute(sql_text, [cfg.printer_id])
        else:
            sql_text = f"""
                SELECT COUNT(*)
                FROM {cfg.queue_table}
                WHERE status = 'queued'
                  AND (next_attempt_at IS NULL OR next_attempt_at <= current_timestamp())
            """
            cursor.execute(sql_text)

        row = cursor.fetchone()
        return int(row[0] if row else 0)


def get_queued_jobs(conn, cfg: AgentConfig, limit: int = 200) -> list[dict[str, Any]]:
    """Return all non-printed jobs (queued, claimed, dead, etc.) for queue visibility."""
    if cfg.local_run:
        jobs = _load_local_queue(cfg)
        results: list[dict[str, Any]] = []
        for job in jobs:
            status = str(job.get("status") or "")
            if status == "printed":
                continue
            try:
                payload = json.loads(job.get("payload_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                payload = {}
            results.append(
                {
                    "status": status,
                    "name": payload.get("name"),
                    "company": payload.get("company"),
                    "attempts": f"{job.get('attempt_count', 0)}/{job.get('max_attempts', '?')}",
                    "created_at": (job.get("created_at") or "")[:19].replace("T", " "),
                    "error": (job.get("error_message") or "")[:80] or None,
                    "job_id": job.get("job_id"),
                }
            )
        results.sort(key=lambda item: _local_sort_key(item, "created_at"))
        return results[: max(1, min(limit, 500))]

    filter_clause = "AND (printer_id = ? OR printer_id IS NULL)" if cfg.printer_id else ""
    params = [cfg.printer_id] if cfg.printer_id else []
    sql_text = f"""
        SELECT job_id, printer_id, attempt_count, created_at, error_message
        FROM {cfg.queue_table}
        WHERE status = 'queued'
          {filter_clause}
          AND (next_attempt_at IS NULL OR next_attempt_at <= current_timestamp())
        ORDER BY created_at ASC
        LIMIT {max(1, min(limit, 500))}
    """
    with conn.cursor() as cursor:
        cursor.execute(sql_text, params)
        rows = cursor.fetchall() or []
    return [
        {
            "job_id": row[0],
            "printer_id": row[1],
            "attempt_count": row[2],
            "created_at": str(row[3]) if row[3] is not None else None,
            "error_message": row[4],
        }
        for row in rows
    ]


def cancel_job(conn, cfg: AgentConfig, job_id: str) -> bool:
    """Mark a job as cancelled so it will not be picked up for printing.
    Returns True if the job was found and updated, False otherwise.
    """
    if cfg.local_run:
        jobs = _load_local_queue(cfg)
        now = _to_iso_z(datetime.now(timezone.utc))
        for job in jobs:
            if str(job.get("job_id") or "") != job_id:
                continue
            # Allow cancelling any non-printed, non-already-cancelled job
            if job.get("status") in ("printed", "cancelled"):
                return False
            job["status"] = "cancelled"
            job["updated_at"] = now
            job["claimed_by"] = None
            job["claimed_at"] = None
            job["claim_expires_at"] = None
            _save_local_queue(cfg, jobs)
            return True
        return False

    with conn.cursor() as cursor:
        sql_text = f"""
            UPDATE {cfg.queue_table}
            SET status = 'cancelled',
                updated_at = current_timestamp(),
                claimed_by = NULL,
                claimed_at = NULL,
                claim_expires_at = NULL
            WHERE job_id = ?
              AND status NOT IN ('printed', 'cancelled')
        """
        cursor.execute(sql_text, [job_id])
        return (cursor.rowcount or 0) > 0


def get_recent_jobs(conn, cfg: AgentConfig, limit: int = 20) -> list[dict[str, Any]]:
    if cfg.local_run:
        jobs = _load_local_queue(cfg)
        jobs.sort(key=lambda item: _local_sort_key(item, "updated_at"), reverse=True)
        results: list[dict[str, Any]] = []
        for job in jobs[: max(1, min(limit, 200))]:
            results.append(
                {
                    "job_id": job.get("job_id"),
                    "status": job.get("status"),
                    "printer_id": job.get("printer_id"),
                    "attempt_count": job.get("attempt_count"),
                    "max_attempts": job.get("max_attempts"),
                    "updated_at": job.get("updated_at"),
                    "error_message": job.get("error_message"),
                }
            )
        return results

    with conn.cursor() as cursor:
        sql_text = f"""
            SELECT job_id, status, printer_id, attempt_count, max_attempts, updated_at, error_message
            FROM {cfg.queue_table}
            ORDER BY updated_at DESC
            LIMIT {max(1, min(limit, 200))}
        """
        cursor.execute(sql_text)
        rows = cursor.fetchall() or []

    results: list[dict[str, Any]] = []
    for row in rows:
        results.append(
            {
                "job_id": row[0],
                "status": row[1],
                "printer_id": row[2],
                "attempt_count": row[3],
                "max_attempts": row[4],
                "updated_at": str(row[5]) if row[5] is not None else None,
                "error_message": row[6],
            }
        )
    return results


def mark_printing(conn, cfg: AgentConfig, job: ClaimedJob) -> None:
    if cfg.local_run:
        jobs = _load_local_queue(cfg)
        now = datetime.now(timezone.utc)
        for item in jobs:
            if str(item.get("job_id") or "") != job.job_id:
                continue
            if str(item.get("claimed_by") or "") != cfg.agent_id:
                return
            item["status"] = "printing"
            item["updated_at"] = _to_iso_z(now)
            item["claim_expires_at"] = _to_iso_z(now + timedelta(seconds=cfg.claim_ttl_seconds))
            _save_local_queue(cfg, jobs)
            return
        return

    with conn.cursor() as cursor:
        sql_text = f"""
            UPDATE {cfg.queue_table}
            SET status = 'printing',
                updated_at = current_timestamp(),
                claim_expires_at = timestampadd(SECOND, ?, current_timestamp())
            WHERE job_id = ? AND claimed_by = ?
        """
        cursor.execute(sql_text, [cfg.claim_ttl_seconds, job.job_id, cfg.agent_id])


def mark_printed(conn, cfg: AgentConfig, job: ClaimedJob) -> None:
    if cfg.local_run:
        jobs = _load_local_queue(cfg)
        now = datetime.now(timezone.utc)
        for item in jobs:
            if str(item.get("job_id") or "") != job.job_id:
                continue
            if str(item.get("claimed_by") or "") != cfg.agent_id:
                return
            item["status"] = "printed"
            item["printed_at"] = _to_iso_z(now)
            item["updated_at"] = _to_iso_z(now)
            item["error_message"] = None
            item["claim_expires_at"] = None
            _save_local_queue(cfg, jobs)
            return
        return

    with conn.cursor() as cursor:
        sql_text = f"""
            UPDATE {cfg.queue_table}
            SET status = 'printed',
                printed_at = current_timestamp(),
                updated_at = current_timestamp(),
                error_message = NULL,
                claim_expires_at = NULL
            WHERE job_id = ? AND claimed_by = ?
        """
        cursor.execute(sql_text, [job.job_id, cfg.agent_id])


def mark_failed(conn, cfg: AgentConfig, job: ClaimedJob, error_message: str) -> None:
    next_attempt = job.attempt_count + 1
    exhausted = next_attempt >= max(1, job.max_attempts)

    base_backoff = min(cfg.max_retry_backoff_seconds, 2 ** max(0, job.attempt_count))
    backoff_seconds = max(cfg.poll_seconds, base_backoff)

    if cfg.local_run:
        jobs = _load_local_queue(cfg)
        now = datetime.now(timezone.utc)
        for item in jobs:
            if str(item.get("job_id") or "") != job.job_id:
                continue
            if str(item.get("claimed_by") or "") != cfg.agent_id:
                return

            if exhausted:
                item["status"] = "dead"
                item["attempt_count"] = next_attempt
                item["updated_at"] = _to_iso_z(now)
                item["claim_expires_at"] = None
                item["error_message"] = error_message[:1500]
                _save_local_queue(cfg, jobs)
                return

            item["status"] = "queued"
            item["attempt_count"] = next_attempt
            item["next_attempt_at"] = _to_iso_z(now + timedelta(seconds=backoff_seconds))
            item["updated_at"] = _to_iso_z(now)
            item["claimed_by"] = None
            item["claimed_at"] = None
            item["claim_expires_at"] = None
            item["error_message"] = error_message[:1500]
            _save_local_queue(cfg, jobs)
            return
        return

    with conn.cursor() as cursor:
        if exhausted:
            sql_text = f"""
                UPDATE {cfg.queue_table}
                SET status = 'dead',
                    attempt_count = ?,
                    updated_at = current_timestamp(),
                    claim_expires_at = NULL,
                    error_message = ?
                WHERE job_id = ? AND claimed_by = ?
            """
            cursor.execute(
                sql_text,
                [next_attempt, error_message[:1500], job.job_id, cfg.agent_id],
            )
            return

        sql_text = f"""
            UPDATE {cfg.queue_table}
            SET status = 'queued',
                attempt_count = ?,
                next_attempt_at = timestampadd(SECOND, ?, current_timestamp()),
                updated_at = current_timestamp(),
                claimed_by = NULL,
                claimed_at = NULL,
                claim_expires_at = NULL,
                error_message = ?
            WHERE job_id = ? AND claimed_by = ?
        """
        cursor.execute(
            sql_text,
            [next_attempt, backoff_seconds, error_message[:1500], job.job_id, cfg.agent_id],
        )


def ensure_printer_connected(client: httpx.Client, cfg: AgentConfig) -> None:
    if not cfg.auto_connect:
        return
    if not cfg.niimbot_address:
        raise RuntimeError("NIIMBOT_ADDRESS is not set for local agent")

    response = client.post(
        f"{cfg.niimbot_server_url}/connect",
        json={"transport": cfg.niimbot_transport, "address": cfg.niimbot_address},
    )
    text = response.text or ""
    if response.status_code != 200 and "Already connected" not in text:
        raise RuntimeError(f"Printer connect failed: {response.status_code} {text}")


def check_printer_bridge(client: httpx.Client, cfg: AgentConfig) -> dict[str, Any]:
    response = client.get(cfg.niimbot_server_url)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        return response.json()
    return {"status_code": response.status_code, "body": response.text[:500]}


def _parse_payload(payload_json: str) -> dict[str, Any]:
    try:
        payload = json.loads(payload_json) if payload_json else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid payload_json: {exc}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("payload_json must decode to a JSON object")
    return payload


def _resolve_print_payload(
    payload: dict[str, Any], cfg: AgentConfig, client: httpx.Client
) -> dict[str, Any]:
    # Allow either nested printRequest or top-level fields.
    print_request = payload.get("printRequest") if isinstance(payload.get("printRequest"), dict) else payload

    image_base64 = print_request.get("imageBase64")
    image_url = print_request.get("imageUrl")

    if not image_base64 and image_url:
        image_response = client.get(str(image_url))
        image_response.raise_for_status()
        image_base64 = base64.b64encode(image_response.content).decode("ascii")

    if not image_base64:
        raise RuntimeError("Job payload must include imageBase64 or imageUrl")

    return {
        "imageBase64": image_base64,
        "labelWidth": int(print_request.get("labelWidth") or cfg.label_width_px),
        "labelHeight": int(print_request.get("labelHeight") or cfg.label_height_px),
        "printTask": str(print_request.get("printTask") or cfg.niimbot_print_task),
        "printDirection": str(
            print_request.get("printDirection") or cfg.niimbot_print_direction
        ),
        "quantity": int(print_request.get("quantity") or cfg.niimbot_default_quantity),
    }


def print_job_with_bridge(client: httpx.Client, cfg: AgentConfig, job: ClaimedJob) -> None:
    payload = _parse_payload(job.payload_json)
    print_payload = _resolve_print_payload(payload, cfg, client)

    ensure_printer_connected(client, cfg)

    response = client.post(f"{cfg.niimbot_server_url}/print", json=print_payload)
    if response.status_code != 200:
        raise RuntimeError(f"Print request failed: {response.status_code} {response.text}")


def process_one_job(
    conn,
    cfg: AgentConfig,
    client: httpx.Client,
    logger: Optional[logging.Logger] = None,
) -> str:
    claimed = claim_next_job(conn, cfg)
    if not claimed:
        return "No queued jobs available"

    if logger:
        logger.info(
            "Claimed job %s (attempt=%s/%s)",
            claimed.job_id,
            claimed.attempt_count + 1,
            claimed.max_attempts,
        )

    try:
        mark_printing(conn, cfg, claimed)
        print_job_with_bridge(client, cfg, claimed)
        mark_printed(conn, cfg, claimed)
        if logger:
            logger.info("Printed job %s", claimed.job_id)
        return f"Printed job {claimed.job_id}"
    except Exception as exc:
        mark_failed(conn, cfg, claimed, str(exc))
        if logger:
            logger.exception("Failed job %s: %s", claimed.job_id, exc)
        raise


def run() -> None:
    cfg = load_config()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logger = logging.getLogger("local_agent")

    logger.info("Starting local print agent")
    if cfg.local_run:
        logger.info("Queue mode: local JSON (%s)", _queue_file_path(cfg))
    else:
        logger.info("Queue table: %s", cfg.queue_table)
    logger.info("Agent ID: %s", cfg.agent_id)
    if cfg.printer_id:
        logger.info("Printer routing ID: %s", cfg.printer_id)

    if cfg.local_run:
        with connect_http_client() as client:
            while True:
                try:
                    message = process_one_job(None, cfg, client, logger)
                    if message == "No queued jobs available":
                        time.sleep(cfg.poll_seconds)
                        continue

                except KeyboardInterrupt:
                    logger.info("Stopping local print agent")
                    break
                except Exception as exc:
                    logger.exception("Worker loop error: %s", exc)
                    time.sleep(cfg.poll_seconds)
        return

    conn = connect_databricks(cfg)
    logger.info("Connected to Databricks SQL")

    with connect_http_client() as client:
        while True:
            try:
                message = process_one_job(conn, cfg, client, logger)
                if message == "No queued jobs available":
                    time.sleep(cfg.poll_seconds)
                    continue

            except KeyboardInterrupt:
                logger.info("Stopping local print agent")
                break
            except Exception as exc:
                logger.exception("Worker loop error: %s", exc)
                time.sleep(cfg.poll_seconds)


if __name__ == "__main__":
    run()
