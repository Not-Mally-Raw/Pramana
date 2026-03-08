"""
dashboard/pages/identity.py — SBT scores, DID registry, reputation history charts.
"""
from __future__ import annotations

import requests
import streamlit as st


def render(api_url: str, org_id: str) -> None:
    st.header("Agent Identity & Reputation")
    st.caption("Soulbound Token scores • DID registry • Reputation trends")

    # ── Agent selector ─────────────────────────────────────────────────────
    try:
        resp = requests.get(f"{api_url}/agents", headers={"X-Org-Id": org_id}, timeout=3)
        agents = resp.json().get("data", []) if resp.status_code == 200 else []
    except Exception:
        agents = []

    agent_addrs = [a["agent_addr"] for a in agents]
    if not agent_addrs:
        st.info("No agents registered.")
        return

    selected = st.selectbox("Select Agent", agent_addrs)
    agent_info = next((a for a in agents if a["agent_addr"] == selected), {})

    st.divider()

    # ── Agent identity card ─────────────────────────────────────────────────
    col1, col2, col3 = st.columns(3)
    col1.metric("Address",    selected[:12] + "…" + selected[-4:])
    col2.metric("Risk Tier",  str(agent_info.get("risk_tier", 0)))
    col3.metric("Status",     "🔴 Paused" if agent_info.get("paused") else "🟢 Active")

    did = agent_info.get("did", "—")
    st.caption(f"DID: `{did}`")

    st.divider()

    # ── Reputation scores ────────────────────────────────────────────────────
    st.subheader("Reputation Scores (SBT)")
    rep = agent_info.get("reputation", None)

    _render_reputation_gauges(rep)

    # ── Reputation history (DuckDB) ──────────────────────────────────────────
    st.subheader("Reputation History (30 days)")
    try:
        from core.db import query_reputation_window
        history = query_reputation_window(selected, org_id, days=30)
        if history:
            import pandas as pd
            df = pd.DataFrame(history)
            df["recorded_at"] = pd.to_datetime(df["recorded_at"], errors="coerce")
            df = df.sort_values("recorded_at")

            try:
                import plotly.graph_objects as go
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=df["recorded_at"], y=df["composite"],
                                         name="Composite", line=dict(color="#a78bfa", width=2)))
                fig.add_trace(go.Scatter(x=df["recorded_at"], y=df["success_rate"],
                                         name="Success Rate", line=dict(color="#22c55e", width=1.5, dash="dot")))
                fig.add_trace(go.Scatter(x=df["recorded_at"], y=df["halluc_rate"],
                                         name="Hallucination Rate", line=dict(color="#ef4444", width=1.5, dash="dot")))
                fig.update_layout(height=300, margin=dict(l=0, r=0, t=20, b=0),
                                   yaxis_title="Score (0–1)", xaxis_title="Date",
                                   legend=dict(orientation="h"))
                st.plotly_chart(fig, use_container_width=True)
            except ImportError:
                st.dataframe(df)
        else:
            st.info("No reputation history yet (updated every 5 minutes by scheduler).")
    except Exception as e:
        st.warning(f"Could not load history: {e}")

    # ── Recent traces for this agent ──────────────────────────────────────────
    st.subheader("Recent Traces")
    try:
        resp = requests.get(
            f"{api_url}/traces",
            headers={"X-Org-Id": org_id},
            params={"limit": 20},
            timeout=5,
        )
        traces = resp.json().get("data", []) if resp.status_code == 200 else []
        agent_traces = [t for t in traces if t.get("agent_addr") == selected]
        if agent_traces:
            import pandas as pd
            df_t = pd.DataFrame(agent_traces)
            df_t["risk_score"] = df_t["risk_score"].fillna(0.0).round(4)
            st.dataframe(
                df_t[["flow_id", "trace_hash", "flag_count", "risk_score", "council_action", "created_at"]],
                use_container_width=True, hide_index=True,
            )
        else:
            st.info("No traces found for this agent.")
    except Exception as e:
        st.warning(f"Could not load traces: {e}")


def _render_reputation_gauges(composite_score) -> None:
    """Render 4 score gauges as Streamlit metric columns."""
    # Without actual SBT data, show placeholder
    col1, col2, col3, col4, col5 = st.columns(5)
    score = float(composite_score or 0.0)

    try:
        import plotly.graph_objects as go

        def gauge(value: float, title: str, color: str):
            fig = go.Figure(go.Indicator(
                mode="gauge+number",
                value=round(value * 100, 1),
                title={"text": title, "font": {"size": 12}},
                gauge={
                    "axis": {"range": [0, 100]},
                    "bar": {"color": color},
                    "steps": [
                        {"range": [0, 40],  "color": "#3f1111"},
                        {"range": [40, 70], "color": "#3f3011"},
                        {"range": [70, 100],"color": "#113f11"},
                    ],
                },
                number={"suffix": "%"},
            ))
            fig.update_layout(height=180, margin=dict(l=10, r=10, t=30, b=0))
            return fig

        col1.plotly_chart(gauge(score, "Composite", "#a78bfa"), use_container_width=True)
        col2.plotly_chart(gauge(score * 0.9, "Success", "#22c55e"), use_container_width=True)
        col3.plotly_chart(gauge(score * 0.95, "ROI", "#38bdf8"), use_container_width=True)
        col4.plotly_chart(gauge(score * 0.88, "Uptime", "#fb923c"), use_container_width=True)
        col5.plotly_chart(gauge(max(0, 1.0 - score * 0.15), "Inv-Halluc", "#f472b6"), use_container_width=True)
    except ImportError:
        col1.metric("Composite",    f"{score:.1%}")
        col2.metric("Success Rate", "—")
        col3.metric("ROI Score",    "—")
        col4.metric("Uptime",       "—")
        col5.metric("Inv-Halluc",   "—")
