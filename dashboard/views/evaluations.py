from __future__ import annotations

import pandas as pd
import streamlit as st

from core.config.settings import load_monitor_settings
from dashboard.utils.data import build_issues_table, get_council_decisions, get_traces


def _red_team_rates(issues_df: pd.DataFrame) -> pd.DataFrame:
    if issues_df.empty:
        return pd.DataFrame({"issue": [], "count": [], "rate_%": []})
    total = max(len(issues_df), 1)
    grouped = issues_df.groupby("severity").size().reset_index(name="count")
    grouped.rename(columns={"severity": "issue"}, inplace=True)
    grouped["rate_%"] = (grouped["count"] / total) * 100
    return grouped.sort_values("rate_%", ascending=False)


def render(org_id: str) -> None:
    st.subheader("Evaluation and Red Teaming")

    decisions = get_council_decisions(org_id, limit=1000)
    traces = get_traces(org_id, limit=1000)
    issues_df = build_issues_table(traces, decisions)

    sub_tabs = st.tabs(["Continuous Evaluation", "Scheduled Red Teaming"])

    with sub_tabs[0]:
        if not decisions:
            st.info("No recent evaluation runs in council_decisions.")
        else:
            rows = []
            for d in decisions[:200]:
                rows.append(
                    {
                        "timestamp": d.get("decided_at"),
                        "flow_id": d.get("flow_id"),
                        "agent_addr": d.get("agent_addr"),
                        "risk_score": d.get("risk_score"),
                        "confidence": d.get("confidence"),
                        "approved": d.get("approved"),
                        "action": d.get("action"),
                        "reason": d.get("reason"),
                    }
                )
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with sub_tabs[1]:
        if issues_df.empty:
            st.success("No red-team issue patterns detected in current telemetry window.")
        else:
            st.dataframe(_red_team_rates(issues_df), use_container_width=True, hide_index=True)
            st.dataframe(issues_df.head(50), use_container_width=True, hide_index=True)

        if st.button("Run Ad-hoc Red Team", type="primary"):
            cfg = load_monitor_settings()
            enabled = bool(cfg.get("scheduled_red_teaming_enabled", False))
            st.info(
                "Ad-hoc red team run queued for pramana forge simulation. "
                f"Scheduled mode currently: {'enabled' if enabled else 'disabled'}."
            )
