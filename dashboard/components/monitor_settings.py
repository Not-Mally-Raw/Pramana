from __future__ import annotations

import streamlit as st

from core.config.settings import invalidate_settings, load_monitor_settings, save_monitor_settings


def _fail_closed_editor(settings_payload: dict) -> dict:
    st.markdown("Fail-Closed Mode per Agent Class")
    cols = st.columns(3)
    classes = ["injection", "hallucination", "network", "operations", "recovery"]
    new_map = dict(settings_payload.get("fail_closed_mode", {}))
    for idx, cls in enumerate(classes):
        col = cols[idx % 3]
        with col:
            new_map[cls] = st.toggle(
                cls.title(),
                value=bool(new_map.get(cls, False)),
                key=f"fc-{cls}",
            )
    return new_map


@st.dialog("Monitor Settings", width="large")
def open_monitor_settings_dialog() -> None:
    current = load_monitor_settings()

    st.text_input("Application Insights Resource", value="pramana-insights", disabled=True)

    st.markdown("Continuous Evaluation")
    continuous_enabled = st.toggle(
        "Enabled",
        value=bool(current.get("continuous_evaluation_enabled", True)),
    )
    sample_rate = st.slider(
        "Sample rate (runs per hour)",
        min_value=1,
        max_value=60,
        value=int(current.get("continuous_sample_rate_per_hour", 6)),
    )

    st.markdown("Scheduled Evaluations")
    scheduled_eval_enabled = st.toggle(
        "Scheduled evaluations enabled",
        value=bool(current.get("scheduled_evaluations_enabled", True)),
    )
    evaluators = st.multiselect(
        "Evaluators",
        options=["Groundedness", "Relevance", "Task-Adherence", "Fluency", "Violence", "Bias", "Safety"],
        default=current.get("evaluators", []),
    )

    st.markdown("Scheduled Red Teaming Runs")
    scheduled_red_team = st.toggle(
        "Scheduled red teaming enabled",
        value=bool(current.get("scheduled_red_teaming_enabled", False)),
    )
    red_team_issues = st.multiselect(
        "Issue families",
        options=["task_adherence", "prohibited_actions", "prompt_injection", "hallucination", "economic_sanity"],
        default=current.get("red_team_issues", []),
    )

    st.markdown("Evaluation Alerts")
    alerts_enabled = st.toggle(
        "Alerts enabled",
        value=bool(current.get("evaluation_alerts_enabled", True)),
    )

    judge_model = st.selectbox(
        "Judge model",
        options=[
            "groq-llama-3.3-70b",
            "llama-3.3-70b-versatile",
            "mixtral-8x7b-32768",
        ],
        index=0,
    )

    st.markdown("Pramana-specific")
    econ_threshold = st.slider(
        "Economic Sanity Threshold",
        min_value=0.0,
        max_value=1.0,
        value=float(current.get("economic_sanity_threshold", 0.75)),
        step=0.01,
    )
    halluc_window = st.number_input(
        "Hallucination Drift Window (seconds)",
        min_value=30,
        max_value=86_400,
        value=int(current.get("hallucination_drift_window_seconds", 900)),
        step=30,
    )
    fail_closed_mode = _fail_closed_editor(current)

    if st.button("Submit", type="primary", use_container_width=True):
        payload = {
            "application_insights": "pramana-insights",
            "continuous_evaluation_enabled": continuous_enabled,
            "continuous_sample_rate_per_hour": sample_rate,
            "scheduled_evaluations_enabled": scheduled_eval_enabled,
            "evaluators": evaluators,
            "scheduled_red_teaming_enabled": scheduled_red_team,
            "red_team_issues": red_team_issues,
            "evaluation_alerts_enabled": alerts_enabled,
            "judge_model": judge_model,
            "economic_sanity_threshold": econ_threshold,
            "hallucination_drift_window_seconds": int(halluc_window),
            "fail_closed_mode": fail_closed_mode,
        }
        save_monitor_settings(payload)
        invalidate_settings()
        st.success("Monitor settings saved and in-memory settings cache refreshed.")
