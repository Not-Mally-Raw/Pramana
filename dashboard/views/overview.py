from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from dashboard.components.assistant import render_ask_ai
from dashboard.components.charts import base_layout, export_controls, make_stacked_area
from dashboard.utils.data import (
    build_error_rate,
    build_overview_metrics,
    build_recent_agents_table,
    build_runs_over_time,
    build_token_cost_trend,
    build_tool_calls_chart,
    get_agents,
    get_traces,
    parse_window,
)


def render(org_id: str, date_choice: str, custom_start=None, custom_end=None) -> None:
    st.subheader("Overview Dashboard")

    window = parse_window(date_choice, custom_start, custom_end)
    agents = get_agents(org_id)
    traces = get_traces(org_id, limit=2000)
    metrics = build_overview_metrics(agents, traces, window)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Agents", metrics["total_agents"], delta="Up" if metrics["total_agents"] else "0")
    c2.metric("Active Agents", metrics["active_agents"], delta="Stable")
    c3.metric("Total Executions", metrics["total_executions"], delta="Live")
    c4.metric("Success Rate %", f"{metrics['success_rate']:.2f}", delta="Healthy" if metrics["success_rate"] >= 70 else "Watch")

    runs_df = build_runs_over_time(traces, window)
    token_df = build_token_cost_trend(traces, window)
    tool_df = build_tool_calls_chart(traces, window)
    err_df = build_error_rate(traces, window)

    row1_left, row1_right = st.columns(2)
    with row1_left:
        st.markdown("#### Agent Runs Over Time")
        fig_runs = make_stacked_area(
            runs_df,
            "date",
            [("started", "#00b5c9"), ("failed", "#ff6b6b"), ("completed", "#32d583")],
        )
        fig_runs.update_layout(**base_layout("Runs status trend", "Runs"))
        fig_runs.update_xaxes(rangeslider_visible=True)
        st.plotly_chart(fig_runs, use_container_width=True)
        export_controls(runs_df, "overview_runs.csv")

    with row1_right:
        st.markdown("#### Token Usage and Cost Trend")
        fig_tokens = go.Figure()
        fig_tokens.add_trace(
            go.Scatter(
                x=token_df["date"],
                y=token_df["tokens"],
                mode="lines+markers",
                name="Total tokens",
                line={"width": 2.5, "color": "#a855f7"},
                fill="tozeroy",
                fillcolor="rgba(168,85,247,0.18)",
            )
        )
        fig_tokens.add_trace(
            go.Scatter(
                x=token_df["date"],
                y=token_df["runs"],
                mode="lines+markers",
                name="Agent runs",
                line={"width": 2.2, "color": "#22c55e"},
            )
        )
        fig_tokens.update_layout(**base_layout("Token and run trend", "Count"))
        st.plotly_chart(fig_tokens, use_container_width=True)
        export_controls(token_df, "overview_tokens_and_cost.csv")

    row2_left, row2_right = st.columns(2)
    with row2_left:
        st.markdown("#### Tool Calls and Agent Runs")
        fig_tools = go.Figure()
        fig_tools.add_trace(
            go.Bar(
                x=tool_df["date"],
                y=tool_df["tool_calls"],
                name="Tool calls",
                marker={"color": "#00e7ff"},
            )
        )
        fig_tools.add_trace(
            go.Bar(
                x=tool_df["date"],
                y=tool_df["runs"],
                name="Runs",
                marker={"color": "#2dd4bf"},
            )
        )
        fig_tools.update_layout(**base_layout("Tool pressure", "Count"), barmode="group")
        st.plotly_chart(fig_tools, use_container_width=True)
        export_controls(tool_df, "overview_tool_calls.csv")

    with row2_right:
        st.markdown("#### Error Rate Trend")
        fig_err = go.Figure()
        fig_err.add_trace(
            go.Scatter(
                x=err_df["date"],
                y=err_df["error_rate"],
                mode="lines+markers",
                name="Error rate %",
                line={"width": 2.2, "color": "#f59e0b"},
                fill="tozeroy",
                fillcolor="rgba(245,158,11,0.22)",
            )
        )
        fig_err.update_layout(**base_layout("Error rate", "%"))
        st.plotly_chart(fig_err, use_container_width=True)
        export_controls(err_df, "overview_error_rate.csv")

    st.markdown("#### Recent Agents")
    recent_agents = build_recent_agents_table(agents, traces)
    if recent_agents.empty:
        st.info("No agents found for current organization.")
    else:
        st.dataframe(recent_agents, use_container_width=True, hide_index=True)
        selected = st.selectbox("Open agent detail", options=recent_agents["Agent ID"].tolist())
        if selected:
            st.session_state["selected_agent"] = selected

    threshold_breach = [t for t in traces[:200] if float(t.get("risk_score") or 0.0) >= 0.7]
    if threshold_breach:
        st.warning("Threshold tuning suggestion: one or more agents exceeded recommended risk range in recent runs.")

    with st.expander("Ask AI", expanded=False):
        render_ask_ai("overview", traces[:50])
