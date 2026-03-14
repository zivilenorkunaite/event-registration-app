"""Simple local UI for the local print agent (Streamlit).

Run:
  streamlit run local_agent/ui.py
"""

from __future__ import annotations

import traceback

import streamlit as st

from local_agent import (
    check_printer_bridge,
    connect_databricks,
    connect_http_client,
    count_queued_jobs,
    ensure_printer_connected,
    get_recent_jobs,
    load_config,
    process_one_job,
)


st.set_page_config(page_title="Local Print Agent", layout="wide")
st.title("Local Print Agent")
st.caption(
    "Runs on your local machine and prints via local niimblue bridge "
    "(queue source: local JSON or Databricks)."
)


@st.cache_resource(show_spinner=False)
def get_cfg():
    return load_config()


try:
    cfg = get_cfg()
except Exception as exc:
    st.error(f"Config error: {exc}")
    st.code(traceback.format_exc())
    st.stop()


left, right = st.columns([1, 1])

with left:
    st.subheader("Configuration")
    queue_source = "local_json" if cfg.local_run else "databricks"
    st.write(
        {
            "queue_source": queue_source,
            "local_queue_file": cfg.local_queue_file if cfg.local_run else "<not used>",
            "queue_table": cfg.queue_table,
            "agent_id": cfg.agent_id,
            "printer_id_filter": cfg.printer_id or "<none>",
            "niimbot_server_url": cfg.niimbot_server_url,
            "niimbot_transport": cfg.niimbot_transport,
            "niimbot_address": cfg.niimbot_address or "<not set>",
            "poll_seconds": cfg.poll_seconds,
        }
    )

with right:
    st.subheader("Actions")

    if st.button("Check printer bridge"):
        try:
            with connect_http_client() as client:
                status = check_printer_bridge(client, cfg)
            st.success("Printer bridge reachable")
            st.json(status)
        except Exception as exc:
            st.error(f"Printer bridge check failed: {exc}")

    if st.button("Connect printer now"):
        try:
            with connect_http_client() as client:
                ensure_printer_connected(client, cfg)
            st.success("Connect call succeeded")
        except Exception as exc:
            st.error(f"Connect failed: {exc}")

    if st.button("Print next queued job"):
        try:
            with connect_databricks(cfg) as conn:
                with connect_http_client() as client:
                    result = process_one_job(conn, cfg, client)
            if result == "No queued jobs available":
                st.info(result)
            else:
                st.success(result)
        except Exception as exc:
            st.error(f"Print run failed: {exc}")
            st.code(traceback.format_exc())

st.divider()

col_a, col_b = st.columns([1, 2])
with col_a:
    st.subheader("Queue")
    if st.button("Refresh queue count"):
        try:
            with connect_databricks(cfg) as conn:
                queued = count_queued_jobs(conn, cfg)
            st.metric("Queued jobs", queued)
        except Exception as exc:
            st.error(f"Count failed: {exc}")

with col_b:
    st.subheader("Recent jobs")
    if st.button("Refresh recent jobs"):
        try:
            with connect_databricks(cfg) as conn:
                jobs = get_recent_jobs(conn, cfg, limit=30)
            st.dataframe(jobs, use_container_width=True)
        except Exception as exc:
            st.error(f"Load recent jobs failed: {exc}")
