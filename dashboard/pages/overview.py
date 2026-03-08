"""
dashboard/pages/overview.py — Live agent table + Plotly gas/risk trend charts.
"""
from __future__ import annotations

import requests
import streamlit as st
import pandas as pd


def render(api_url: str, org_id: str) -> None:
    st.header("Agent Overview")

    # ── Metrics row ───────────────────────────────────────────────────────────
    col1, col2, col3, col4 = st.columns(4)
    try:
        resp = requests.get(f"{api_url}/agents", headers={"X-Org-Id": org_id}, timeout=3)
        agents = resp.json().get("data", []) if resp.status_code == 200 else []
    except Exception:
        agents = []

    try:
        resp = requests.get(f"{api_url}/traces", headers={"X-Org-Id": org_id}, timeout=3, params={"limit": 200})
        traces = resp.json().get("data", []) if resp.status_code == 200 else []
    except Exception:
        traces = []

    total = len(agents)
    paused = sum(1 for a in agents if a.get("paused"))
    flagged_traces = sum(1 for t in traces if t.get("flag_count", 0) > 0)
    avg_risk = (sum(t.get("risk_score", 0) for t in traces) / len(traces)) if traces else 0.0

    col1.metric("Total Agents",    total)
    col2.metric("Paused Agents",   paused, delta=f"-{paused}" if paused else None)
    col3.metric("Flagged Traces",  flagged_traces)
    col4.metric("Avg Risk Score",  f"{avg_risk:.2%}")

    st.divider()

    # ── Agent table ───────────────────────────────────────────────────────────
    st.subheader("Registered Agents")
    if agents:
        df = pd.DataFrame(agents)
        df["paused"] = df["paused"].apply(lambda x: "🔴 Paused" if x else "🟢 Active")
        df["risk_tier"] = df.get("risk_tier", pd.Series([0]*len(df))).fillna(0).astype(int)
        df["reputation"] = df.get("reputation", pd.Series([0.0]*len(df))).fillna(0.0).round(3)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No agents registered. POST /agents/register to add agents.")

    st.divider()

    # ── Recent traces ─────────────────────────────────────────────────────────
    st.subheader("Recent Traces")
    if traces:
        df_t = pd.DataFrame(traces[:50])
        df_t["risk_score"] = df_t["risk_score"].fillna(0.0).round(4)
        df_t["verdict"] = df_t["council_action"].apply(
            lambda x: "✅ PASS" if x == "FlowAction.PASS" else ("🔴 PAUSE" if "PAUSE" in str(x) else "⚠️ " + str(x))
        )
        st.dataframe(
            df_t[["flow_id", "agent_addr", "trace_hash", "flag_count", "risk_score", "verdict", "created_at"]],
            use_container_width=True,
            hide_index=True,
        )

        # ── Risk score trend ─────────────────────────────────────────────────
        st.subheader("Risk Score Trend")
        try:
            import plotly.graph_objects as go
            df_chart = df_t.copy()
            df_chart["created_at"] = pd.to_datetime(df_chart["created_at"], errors="coerce")
            df_chart = df_chart.sort_values("created_at")

            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=df_chart["created_at"],
                y=df_chart["risk_score"],
                mode="lines+markers",
                name="Risk Score",
                line=dict(color="#EF4444", width=2),
                marker=dict(size=5),
            ))
            fig.add_hline(y=0.70, line_dash="dash", line_color="red",   annotation_text="Pause threshold (0.70)")
            fig.add_hline(y=0.40, line_dash="dot",  line_color="orange", annotation_text="Escalate threshold (0.40)")
            fig.update_layout(height=300, margin=dict(l=0, r=0, t=20, b=0),
                              xaxis_title="Time", yaxis_title="Risk Score (0–1)")
            st.plotly_chart(fig, use_container_width=True)
        except ImportError:
            st.warning("Install plotly for charts: pip install plotly")
    else:
        st.info("No traces yet. Ingest events via POST /ingest or start the chain listener.")

    # ── Manual event injection ────────────────────────────────────────────────
    st.divider()
    st.subheader("Inject Test Event")
    with st.expander("Simulate a chain event"):
        with st.form("ingest_form"):
            agent_addr = st.text_input("Agent Address", "0x0000000000000000000000000000000000000001")
            action     = st.text_input("Action", "transfer")
            calldata   = st.text_area("Calldata (JSON or hex)", '{"to": "0xABCDEF", "value": 100}')
            value_eth  = st.number_input("Value (ETH)", 0.0, 1000.0, 0.0, step=0.01)
            submitted  = st.form_submit_button("Ingest")
        if submitted:
            try:
                resp = requests.post(
                    f"{api_url}/ingest",
                    json={
                        "agent_id": agent_addr,
                        "org_id": org_id,
                        "action": action,
                        "calldata": calldata,
                        "value_wei": int(value_eth * 1e18),
                        "network": "anvil",
                    },
                    headers={"X-Org-Id": org_id},
                    timeout=15,
                )
                if resp.status_code == 200:
                    result = resp.json()
                    st.success(f"Flow complete: {result['data'].get('action')} | risk={result['data'].get('risk_score', 0):.2%}")
                    st.json(result["data"])
                else:
                    st.error(f"API error {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                st.error(f"Failed: {e}")
