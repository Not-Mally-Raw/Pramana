"""
dashboard/pages/replay.py — TraceDAG replay viewer.
Load a trace by flow_id from SQLite and visualize the interaction DAG.
"""
from __future__ import annotations

import json
import requests
import streamlit as st


def render(api_url: str, org_id: str) -> None:
    st.header("Trace Replay")
    st.caption("Inspect and replay TraceDAG for any past flow execution.")

    # ── Flow selector ─────────────────────────────────────────────────────────
    col_a, col_b = st.columns([3, 1])
    with col_a:
        flow_id = st.text_input("Flow ID", placeholder="Enter flow_id from traces list")
    with col_b:
        st.write("")
        st.write("")
        load = st.button("Load Trace")

    # ── Recent traces quick-select ────────────────────────────────────────────
    try:
        resp = requests.get(f"{api_url}/traces", headers={"X-Org-Id": org_id}, timeout=3, params={"limit": 10})
        recent = resp.json().get("data", []) if resp.status_code == 200 else []
    except Exception:
        recent = []

    if recent:
        st.caption("Recent traces (click to prefill):")
        cols = st.columns(min(5, len(recent)))
        for i, t in enumerate(recent[:5]):
            if cols[i].button(t["flow_id"][:12] + "…", key=f"trace_{i}"):
                flow_id = t["flow_id"]
                load = True

    if not load or not flow_id:
        return

    # ── Fetch trace ───────────────────────────────────────────────────────────
    try:
        resp = requests.get(
            f"{api_url}/replay/{flow_id}",
            headers={"X-Org-Id": org_id},
            timeout=5,
        )
        if resp.status_code == 404:
            st.error(f"Trace '{flow_id}' not found.")
            return
        if resp.status_code != 200:
            st.error(f"API error {resp.status_code}")
            return
        trace = resp.json()["data"]
    except Exception as e:
        st.error(f"Failed: {e}")
        return

    # ── Metadata ──────────────────────────────────────────────────────────────
    st.subheader("Trace Metadata")
    meta_col1, meta_col2, meta_col3, meta_col4 = st.columns(4)
    meta_col1.metric("Flow ID",     trace.get("flow_id", "")[:16] + "…")
    meta_col2.metric("Flags",       trace.get("flag_count", 0))
    meta_col3.metric("Risk Score",  f"{trace.get('risk_score', 0):.2%}")
    meta_col4.metric("Verdict",     trace.get("council_action", "—").replace("FlowAction.", ""))
    st.caption(f"Agent: `{trace.get('agent_addr', '—')}`  |  Hash: `{trace.get('trace_hash', '—')}`  |  Created: `{trace.get('created_at', '—')}`")

    st.divider()

    # ── DAG visualization ─────────────────────────────────────────────────────
    dag = trace.get("dag")
    if dag:
        st.subheader("Interaction DAG")
        _render_dag(dag)
    else:
        st.info("No DAG data stored for this trace.")

    # ── Raw JSON ──────────────────────────────────────────────────────────────
    with st.expander("Raw Trace JSON"):
        st.json(trace)


def _render_dag(dag: dict) -> None:
    nodes = dag.get("nodes", {})
    edges = dag.get("edges", [])

    if not nodes:
        st.info("Empty DAG.")
        return

    # ── Node table ────────────────────────────────────────────────────────────
    st.write("**Nodes:**")
    rows = []
    for node_id, attrs in nodes.items():
        rows.append({
            "node":    node_id,
            "action":  attrs.get("action", ""),
            "score":   f"{attrs.get('score', 0):.3f}",
            "flagged": "🔴" if attrs.get("flagged") else "🟢",
            "reason":  attrs.get("metadata", {}).get("reason", ""),
            "latency": f"{attrs.get('metadata', {}).get('latency_ms', 0):.1f}ms",
        })
    import pandas as pd
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # ── Graphviz DAG ──────────────────────────────────────────────────────────
    try:
        import graphviz
        dot = graphviz.Digraph(graph_attr={"rankdir": "LR", "bgcolor": "#0f172a"})
        dot.attr("node", style="filled", fontcolor="white", fontsize="12")
        dot.attr("edge", color="#64748b")

        for node_id, attrs in nodes.items():
            color = "#EF4444" if attrs.get("flagged") else "#22c55e"
            label = f"{node_id}\nscore={attrs.get('score', 0):.2f}"
            dot.node(node_id, label=label, fillcolor=color)

        for edge in edges:
            dot.edge(str(edge[0]), str(edge[1]))

        st.graphviz_chart(dot.source, use_container_width=True)
    except ImportError:
        st.info("Install graphviz for DAG visualization: pip install graphviz")
    except Exception as e:
        st.warning(f"DAG render failed: {e}")
