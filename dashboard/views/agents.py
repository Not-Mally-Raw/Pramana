from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.components.assistant import render_ask_ai
from dashboard.components.charts import base_layout, export_controls, status_dot
from dashboard.utils.data import (
    build_issues_table,
    build_workflow_waterfall,
    get_agent_detail,
    get_agents,
    monitoring_systems_status,
)


def _waterfall_color(rel: float) -> str:
    if rel >= 90:
        return "#32d583"
    if rel >= 70:
        return "#f59e0b"
    return "#ff6b6b"


def render(org_id: str) -> None:
    st.subheader("Agent Detail")
    agents = get_agents(org_id)
    if not agents:
        st.info("No agents available.")
        return

    default_agent = st.session_state.get("selected_agent")
    options = [a.get("agent_addr") for a in agents]
    index = options.index(default_agent) if default_agent in options else 0
    selected_agent = st.selectbox("Select Agent", options=options, index=index)

    detail = get_agent_detail(selected_agent, org_id)
    metrics = detail["metrics"]

    badge_html = status_dot("active" if metrics["success_rate"] >= 70 else "degraded")
    st.markdown(
        f"<div class='pm-card'><h3 style='margin:0'>{selected_agent}</h3>{badge_html} Last Updated: {metrics['last_execution']}</div>",
        unsafe_allow_html=True,
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Executions", metrics["total_executions"])
    m2.metric("Success Rate", f"{metrics['success_rate']:.2f}%")
    m3.metric("Avg Execution Time", f"{metrics['avg_execution_time']:.3f}s")
    m4.metric("Last Execution", str(metrics["last_execution"])[:19])

    left, right = st.columns([3, 2])
    with left:
        st.markdown("#### Workflow Execution Waterfall")
        wf = build_workflow_waterfall(detail["traces"])
        if wf.empty:
            st.info("No workflow nodes found in traces.")
        else:
            fig = go.Figure(
                go.Bar(
                    x=wf["reliability"],
                    y=wf["step"],
                    orientation="h",
                    text=wf["reliability"].round(1).astype(str) + "%",
                    textposition="inside",
                    marker={"color": [_waterfall_color(v) for v in wf["reliability"]]},
                    hovertemplate="Step: %{y}<br>Reliability: %{x:.2f}%<br>Avg duration: %{customdata:.2f} ms<extra></extra>",
                    customdata=wf["duration_ms"],
                )
            )
            fig.update_layout(**base_layout("Execution reliability by step", "Reliability %"))
            st.plotly_chart(fig, use_container_width=True)
            export_controls(wf, "agent_workflow_waterfall.csv")

    with right:
        st.markdown("#### Agent Assistant")
        st.caption("Prompt templates")
        st.write("- Explain this agent's recent failures")
        st.write("- Suggest better policy thresholds")
        st.write("- Show me the last council vote details")
        render_ask_ai("agent-detail", detail["traces"][:40])

    deploy_col, monitor_col = st.columns(2)
    with deploy_col:
        st.markdown("#### Deployment")
        networks = detail.get("deployment_networks", [])
        if networks:
            st.write("Networks:", ", ".join(networks))
        else:
            st.write("Networks: unknown")
        st.caption("Derived from event telemetry and current config network.")

    with monitor_col:
        st.markdown("#### Monitoring Systems")
        monitor_rows = monitoring_systems_status()
        st.dataframe(pd.DataFrame(monitor_rows), use_container_width=True, hide_index=True)

    tabs = st.tabs(["Executions", "Evaluation Metrics", "Trace Replay", "Issues"])

    with tabs[0]:
        execution_rows = []
        decisions_by_flow = {d.get("flow_id"): d for d in detail["decisions"]}
        for t in detail["traces"][:120]:
            d = decisions_by_flow.get(t.get("flow_id"), {})
            execution_rows.append(
                {
                    "timestamp": t.get("created_at"),
                    "risk_score": t.get("risk_score"),
                    "council_decision": t.get("council_action"),
                    "confidence": d.get("confidence"),
                    "trace_link": t.get("flow_id"),
                }
            )
        if execution_rows:
            st.dataframe(pd.DataFrame(execution_rows), use_container_width=True, hide_index=True)
        else:
            st.info("No executions available.")

    with tabs[1]:
        eval_rows = []
        for d in detail["decisions"][:120]:
            eval_rows.append(
                {
                    "decided_at": d.get("decided_at"),
                    "coherence": round(float(d.get("confidence") or 0.0), 3),
                    "fluency": round(max(0.0, min(1.0, 1.0 - float(d.get("risk_score") or 0.0))), 3),
                    "violence": round(min(1.0, float(d.get("risk_score") or 0.0) * 0.7), 3),
                    "reason": d.get("reason"),
                }
            )
        if eval_rows:
            st.dataframe(pd.DataFrame(eval_rows), use_container_width=True, hide_index=True)
        else:
            st.info("No evaluation records found.")

    with tabs[2]:
        if not detail["traces"]:
            st.info("No traces to replay.")
        else:
            st.caption("Replay Last 10 Executions")
            if st.button("Replay Last 10 Executions"):
                st.session_state["replay_flow_ids"] = [t.get("flow_id") for t in detail["traces"][:10]]
            replay_rows = [
                {
                    "flow_id": t.get("flow_id"),
                    "trace_hash": t.get("trace_hash"),
                    "created_at": t.get("created_at"),
                }
                for t in detail["traces"][:10]
            ]
            st.dataframe(pd.DataFrame(replay_rows), use_container_width=True, hide_index=True)

    with tabs[3]:
        issues_df = build_issues_table(detail["traces"], detail["decisions"])
        if issues_df.empty:
            st.success("No high-signal issues detected for this agent in recent traces.")
        else:
            st.dataframe(issues_df, use_container_width=True, hide_index=True)
            export_controls(issues_df, "agent_issues.csv")
