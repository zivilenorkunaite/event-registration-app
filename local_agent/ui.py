"""Local print agent UI (Streamlit).

Run:
  streamlit run local_agent/ui.py
"""

from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime, timezone

import streamlit as st

from local_agent import (
    cancel_job,
    check_printer_bridge,
    connect_databricks,
    connect_http_client,
    get_queued_jobs,
    get_recent_jobs,
    load_config,
    process_one_job,
)

st.set_page_config(page_title="Print Agent", layout="wide")


# ── Compatibility helpers ─────────────────────────────────────────────────────

def _cache_resource_compat(func):
    if hasattr(st, "cache_resource"):
        return st.cache_resource(show_spinner=False)(func)
    if hasattr(st, "experimental_singleton"):
        return st.experimental_singleton(show_spinner=False)(func)
    return func


def _dataframe_compat(data) -> None:
    try:
        st.dataframe(data, use_container_width=True)
    except TypeError:
        st.dataframe(data)


def _rerun() -> None:
    if hasattr(st, "rerun"):
        st.rerun()
    elif hasattr(st, "experimental_rerun"):
        st.experimental_rerun()


def _ago(ts: float | None) -> str:
    if ts is None:
        return "never"
    secs = int(time.time() - ts)
    if secs < 5:
        return "just now"
    if secs < 60:
        return f"{secs}s ago"
    return f"{secs // 60}m {secs % 60}s ago"


# ── Shared agent state (lives for the Streamlit session lifetime) ─────────────

@_cache_resource_compat
def _get_state() -> dict:
    return {
        "paused": False,
        "poll_interval": 5,        # seconds between polls
        "last_poll_at": None,      # time.time() float
        "last_result": None,       # str
        "last_error": None,        # str
        "printer_ok": None,        # True / False / None = unknown
        "printer_info": None,      # dict from bridge
        "queued_jobs": [],
        "recent_jobs": [],
        "print_count": 0,
        "thread_started": False,
        "_lock": threading.Lock(),
    }


@_cache_resource_compat
def _get_cfg():
    return load_config()


def _poll_loop(cfg, state: dict) -> None:
    """Background thread: poll and print until process exits."""
    while True:
        if state["paused"]:
            time.sleep(1)
            continue

        try:
            # Refresh printer status every poll
            try:
                with connect_http_client() as client:
                    info = check_printer_bridge(client, cfg)
                with state["_lock"]:
                    state["printer_ok"] = True
                    state["printer_info"] = info
            except Exception as pe:
                with state["_lock"]:
                    state["printer_ok"] = False
                    state["printer_info"] = {"error": str(pe)}

            # Attempt to print one job
            with connect_databricks(cfg) as conn:
                with connect_http_client() as client:
                    result = process_one_job(conn, cfg, client)

            with state["_lock"]:
                state["last_poll_at"] = time.time()
                state["last_result"] = result
                state["last_error"] = None
                if result and result != "No queued jobs available":
                    state["print_count"] += 1

            # Refresh queue & recent jobs
            try:
                with connect_databricks(cfg) as conn:
                    qj = get_queued_jobs(conn, cfg)
                    rj = get_recent_jobs(conn, cfg, limit=20)
                with state["_lock"]:
                    state["queued_jobs"] = qj
                    state["recent_jobs"] = rj
            except Exception:
                pass

        except Exception as exc:
            with state["_lock"]:
                state["last_poll_at"] = time.time()
                state["last_error"] = str(exc)

        interval = state.get("poll_interval", 5)
        time.sleep(max(1, interval))


def _ensure_thread(cfg, state: dict) -> None:
    with state["_lock"]:
        if state["thread_started"]:
            return
        state["thread_started"] = True

    t = threading.Thread(target=_poll_loop, args=(cfg, state), daemon=True)
    t.start()


# ── Load config / state ───────────────────────────────────────────────────────

try:
    cfg = _get_cfg()
except Exception as exc:
    st.error(f"Config error: {exc}")
    st.code(traceback.format_exc())
    st.stop()

state = _get_state()
_ensure_thread(cfg, state)

# ── Header ────────────────────────────────────────────────────────────────────

col_title, col_badge = st.columns([4, 1])
with col_title:
    st.title("Print Agent")
with col_badge:
    st.write("")  # spacer
    if state["paused"]:
        st.error("⏸ PAUSED")
    else:
        st.success("▶ POLLING")

st.markdown("---")

# ── Printer + Polling controls (side by side) ─────────────────────────────────

left, right = st.columns([1, 1])

with left:
    st.subheader("Printer")
    pok = state["printer_ok"]
    pinfo = state["printer_info"] or {}
    if pok is None:
        st.info("Waiting for first poll…")
    elif pok:
        st.success("Bridge reachable")
        if pinfo:
            st.json(pinfo)
    else:
        err = pinfo.get("error", "unknown error")
        st.error(f"Bridge unreachable: {err}")

with right:
    st.subheader("Polling")

    # Pause / Resume
    paused_now = state["paused"]
    btn_label = "▶ Resume polling" if paused_now else "⏸ Pause polling"
    if st.button(btn_label):
        with state["_lock"]:
            state["paused"] = not state["paused"]
        _rerun()

    # Poll interval slider
    current_interval = state.get("poll_interval", 5)
    new_interval = st.slider(
        "Poll interval (seconds)",
        min_value=1,
        max_value=60,
        value=current_interval,
        step=1,
    )
    if new_interval != current_interval:
        with state["_lock"]:
            state["poll_interval"] = new_interval

    # Status summary
    st.write(f"**Last poll:** {_ago(state['last_poll_at'])}")
    if state["last_error"]:
        st.warning(f"Last error: {state['last_error']}")
    elif state["last_result"]:
        result_display = state["last_result"]
        if result_display == "No queued jobs available":
            st.write("Last result: queue empty")
        else:
            st.write(f"Last result: {result_display}")
    st.write(f"**Printed this session:** {state['print_count']}")

st.markdown("---")

# ── Queue ─────────────────────────────────────────────────────────────────────

all_jobs    = state["queued_jobs"]
waiting     = [j for j in all_jobs if j.get("status") == "queued"]
in_progress = [j for j in all_jobs if j.get("status") in ("claimed", "printing")]
queue_display = waiting + in_progress  # only active jobs shown here

q_count = len(waiting) + len(in_progress)
st.subheader(f"Queue  — {q_count} waiting" if q_count else "Queue  — empty")

# Confirmation state lives in session_state so it survives auto-reruns
if "confirm_cancel_id" not in st.session_state:
    st.session_state["confirm_cancel_id"] = None
if "confirm_cancel_label" not in st.session_state:
    st.session_state["confirm_cancel_label"] = ""

# ── Inline confirmation banner ────────────────────────────────────────────────
pending_id = st.session_state["confirm_cancel_id"]
if pending_id:
    label = st.session_state["confirm_cancel_label"]
    st.warning(f"Cancel **{label}**?  This job will not be printed unless resubmitted from the app.")
    conf_col1, conf_col2, _ = st.columns([1, 1, 6])
    with conf_col1:
        if st.button("✅ Yes, cancel it"):
            try:
                with connect_databricks(cfg) as conn:
                    cancel_job(conn, cfg, pending_id)
                # Refresh queue immediately
                with connect_databricks(cfg) as conn:
                    qj = get_queued_jobs(conn, cfg)
                    rj = get_recent_jobs(conn, cfg, limit=20)
                with state["_lock"]:
                    state["queued_jobs"] = qj
                    state["recent_jobs"] = rj
            except Exception as exc:
                st.error(f"Cancel failed: {exc}")
            st.session_state["confirm_cancel_id"] = None
            st.session_state["confirm_cancel_label"] = ""
            _rerun()
    with conf_col2:
        if st.button("❌ No, keep it"):
            st.session_state["confirm_cancel_id"] = None
            st.session_state["confirm_cancel_label"] = ""
            _rerun()

# ── Job rows (active only) ────────────────────────────────────────────────────
STATUS_COLOR = {
    "queued":   "🟡",
    "claimed":  "🔵",
    "printing": "🔵",
    "printed":  "🟢",
    "dead":     "🔴",
    "cancelled":"⚫",
}

if queue_display:
    h1, h2, h3, h4, h5, h6, h7 = st.columns([1, 2, 2, 1, 2, 3, 1])
    h1.markdown("**Status**")
    h2.markdown("**Name**")
    h3.markdown("**Company**")
    h4.markdown("**Tries**")
    h5.markdown("**Created**")
    h6.markdown("**Error**")
    h7.markdown("**Action**")
    st.markdown("---")

    for job in queue_display:
        job_id  = job.get("job_id", "")
        status  = job.get("status", "")
        icon    = STATUS_COLOR.get(status, "⚪")

        c1, c2, c3, c4, c5, c6, c7 = st.columns([1, 2, 2, 1, 2, 3, 1])
        c1.write(f"{icon} {status}")
        c2.write(job.get("name") or "—")
        c3.write(job.get("company") or "—")
        c4.write(job.get("attempts") or "—")
        c5.write(job.get("created_at") or "—")
        c6.write((job.get("error") or "")[:60] or "—")
        btn_key = f"cancel__{job_id}"
        if c7.button("🚫", key=btn_key, help="Cancel this job"):
            name_label = f"{job.get('name') or '?'} / {job.get('company') or '?'}"
            st.session_state["confirm_cancel_id"] = job_id
            st.session_state["confirm_cancel_label"] = name_label
            _rerun()
else:
    st.info("No jobs waiting to print.")

st.markdown("---")

# ── Recent jobs ───────────────────────────────────────────────────────────────

# Build status summary from all_jobs (includes dead, cancelled etc.)
dead      = [j for j in all_jobs if j.get("status") == "dead"]
cancelled = [j for j in all_jobs if j.get("status") == "cancelled"]
printed   = [j for j in state["recent_jobs"] if j.get("status") == "printed"]

summary_parts = []
if printed:     summary_parts.append(f"{len(printed)} printed")
if dead:        summary_parts.append(f"{len(dead)} dead")
if cancelled:   summary_parts.append(f"{len(cancelled)} cancelled")
summary_str = " · ".join(summary_parts) if summary_parts else "none yet"

st.subheader(f"Recent jobs  — {summary_str}")
recent_jobs = state["recent_jobs"]
if recent_jobs:
    _dataframe_compat(recent_jobs)
else:
    st.info("No recent jobs yet.")

# ── Manual controls ───────────────────────────────────────────────────────────

st.markdown("---")
with st.expander("Manual controls"):
    if st.button("Print next job now"):
        try:
            with connect_databricks(cfg) as conn:
                with connect_http_client() as client:
                    res = process_one_job(conn, cfg, client)
            if res == "No queued jobs available":
                st.info(res)
            else:
                st.success(res)
                with state["_lock"]:
                    state["print_count"] += 1
        except Exception as exc:
            st.error(f"Failed: {exc}")
            st.code(traceback.format_exc())

    if st.button("Refresh queue & jobs now"):
        try:
            with connect_databricks(cfg) as conn:
                qj = get_queued_jobs(conn, cfg)
                rj = get_recent_jobs(conn, cfg, limit=20)
            with state["_lock"]:
                state["queued_jobs"] = qj
                state["recent_jobs"] = rj
            _rerun()
        except Exception as exc:
            st.error(f"Refresh failed: {exc}")

# ── Auto-refresh the page ─────────────────────────────────────────────────────

if not state["paused"]:
    refresh_secs = max(3, state.get("poll_interval", 5))
    time.sleep(refresh_secs)
    _rerun()


