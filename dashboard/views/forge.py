from __future__ import annotations

import pandas as pd
import streamlit as st

from core.config.settings import load_monitor_settings


def render() -> None:
    st.subheader("Forge Simulation")
    st.caption("Adversarial simulation and replay control plane.")

    cfg = load_monitor_settings()
    issues = cfg.get("red_team_issues", [])

    c1, c2, c3 = st.columns(3)
    c1.metric("Configured issue families", len(issues))
    c2.metric("Continuous evaluation", "On" if cfg.get("continuous_evaluation_enabled") else "Off")
    c3.metric("Red teaming schedule", "On" if cfg.get("scheduled_red_teaming_enabled") else "Off")

    st.markdown("#### Configured attack suites")
    if issues:
        st.dataframe(pd.DataFrame({"issue_family": issues}), use_container_width=True, hide_index=True)
    else:
        st.info("No issue families selected. Use Monitor Settings to configure red-team scenarios.")

    with st.expander("Run simulation"):
        target = st.text_input("Target agent", value=st.session_state.get("selected_agent", ""))
        rounds = st.slider("Rounds", min_value=1, max_value=50, value=5)
        if st.button("Launch simulation"):
            st.success(f"Simulation request prepared for {target or 'all agents'} across {rounds} rounds.")
