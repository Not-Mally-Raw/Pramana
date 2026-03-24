"""Pramana Monitor main Streamlit app shell."""

from __future__ import annotations

import os
import sys

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.config.settings import get_settings
from dashboard.components.monitor_settings import open_monitor_settings_dialog
from dashboard.components.theme import apply_theme, footer, schedule_rerun
from dashboard.views import agents, evaluations, forge, overview


def _sidebar(settings) -> tuple[str, str, object, object]:
    with st.sidebar:
        st.title("Pramana Monitor")
        st.caption("Production AgentOps for on-chain and off-chain flows")
        st.divider()

        org_id = st.text_input("Org ID", value=settings.DEFAULT_ORG_ID)

        st.markdown("#### Date Range")
        date_choice = st.selectbox("Window", ["Last Day", "Last 7D", "Last 1M", "Custom"], index=1)
        custom_start = None
        custom_end = None
        if date_choice == "Custom":
            c1, c2 = st.columns(2)
            custom_start = c1.date_input("Start")
            custom_end = c2.date_input("End")

        st.markdown("#### Navigation")
        page = st.radio(
            "Pages",
            ["Dashboard", "Agents", "Monitor Settings", "Evaluations", "Forge"],
            label_visibility="collapsed",
        )

        st.markdown("#### Ask AI")
        st.caption("Use page-level Ask AI panels for contextual answers.")

        return org_id, page, date_choice, (custom_start, custom_end)


def main() -> None:
    settings = get_settings()
    apply_theme()
    schedule_rerun(seconds=15)

    org_id, page, date_choice, custom_dates = _sidebar(settings)
    custom_start, custom_end = custom_dates

    top_l, top_r = st.columns([8, 1])
    with top_l:
        st.markdown("## Pramana Monitor")
    with top_r:
        if st.button("Settings", use_container_width=True):
            open_monitor_settings_dialog()

    if page == "Dashboard":
        overview.render(org_id, date_choice, custom_start, custom_end)
    elif page == "Agents":
        agents.render(org_id)
    elif page == "Monitor Settings":
        st.subheader("Monitor Settings")
        st.write("Use the button below to open the full settings panel.")
        if st.button("Open Monitor Settings Panel", type="primary"):
            open_monitor_settings_dialog()
    elif page == "Evaluations":
        evaluations.render(org_id)
    else:
        forge.render()

    footer(settings.APP_VERSION)


if __name__ == "__main__":
    main()
