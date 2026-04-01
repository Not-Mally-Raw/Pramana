"""
dashboard/views/recovery.py — Phase 3 Recovery Alerts Visualization
Shows recovery alerts for paused agents with open positions.
"""
from __future__ import annotations

import json
import streamlit as st
import pandas as pd
from dashboard.utils.data import parse_window


def render(org_id: str, date_choice: str, custom_start=None, custom_end=None) -> None:
    st.subheader("Recovery Alerts")
    st.markdown("Position risk classifications for paused agents.")

    window = parse_window(date_choice, custom_start, custom_end)

    # Fetch recovery alerts from database
    try:
        from core.db import get_sqlite_conn
        with get_sqlite_conn() as conn:
            rows = conn.execute("""
                SELECT alert_id, agent_addr, org_id, pause_timestamp,
                       total_usd_at_risk, critical_count, warning_count, safe_count,
                       webhook_delivered, created_at
                FROM recovery_alerts
                WHERE org_id=? AND created_at >= ?
                ORDER BY created_at DESC
                LIMIT 100
            """, (org_id, window["start"])).fetchall()

        if not rows:
            st.info("No recovery alerts in this period.")
            return

        # Summary metrics
        c1, c2, c3, c4 = st.columns(4)
        total_alerts = len(rows)
        total_usd_at_risk = sum(r["total_usd_at_risk"] for r in rows)
        critical_count_total = sum(r["critical_count"] for r in rows)
        webhook_success = sum(1 for r in rows if r["webhook_delivered"])

        c1.metric("Total Alerts", total_alerts)
        c2.metric("Total USD at Risk", f"${total_usd_at_risk:,.0f}")
        c3.metric("Critical Positions", critical_count_total)
        c4.metric("Webhooks Delivered", f"{webhook_success}/{total_alerts}")

        # Alerts table
        st.markdown("#### Recent Recovery Alerts")
        df = pd.DataFrame([dict(r) for r in rows])
        df["pause_time"] = pd.to_datetime(df["pause_timestamp"], unit="s").dt.strftime("%Y-%m-%d %H:%M:%S")
        df["risk_summary"] = df.apply(
            lambda r: f"🔴 {r['critical_count']} | 🟡 {r['warning_count']} | 🟢 {r['safe_count']}",
            axis=1
        )

        display_cols = ["agent_addr", "risk_summary", "total_usd_at_risk", "webhook_delivered", "pause_time"]
        df_display = df[display_cols].copy()
        df_display.columns = ["Agent", "Risk Levels (Crit | Warn | Safe)", "USD at Risk", "Webhook Delivered", "Pause Time"]

        st.dataframe(df_display, use_container_width=True)

        # Detailed alert breakdown
        st.markdown("#### Alert Details")
        selected_alert = st.selectbox(
            "Select an alert to view details",
            options=[r["alert_id"] for r in rows],
            format_func=lambda x: next(r for r in rows if r["alert_id"] == x)["agent_addr"]
        )

        selected_row = next(r for r in rows if r["alert_id"] == selected_alert)
        with st.expander(f"Details for {selected_row['agent_addr']}"):
            st.json({
                "alert_id": selected_row["alert_id"],
                "agent_addr": selected_row["agent_addr"],
                "total_usd_at_risk": selected_row["total_usd_at_risk"],
                "critical_positions": selected_row["critical_count"],
                "warning_positions": selected_row["warning_count"],
                "safe_positions": selected_row["safe_count"],
                "webhook_delivered": selected_row["webhook_delivered"],
                "pause_timestamp": selected_row["pause_timestamp"]
            })

            # Try to show classifications if available
            try:
                with get_sqlite_conn() as conn:
                    alert_row = conn.execute(
                        "SELECT classifications_json FROM recovery_alerts WHERE alert_id=?",
                        (selected_alert,)
                    ).fetchone()
                    if alert_row and alert_row["classifications_json"]:
                        classifications = json.loads(alert_row["classifications_json"])
                        st.markdown("**Position Classifications:**")
                        for cls in classifications:
                            risk_emoji = {"CRITICAL": "🔴", "WARNING": "🟡", "SAFE": "🟢"}.get(cls.get("risk_level"), "⚪")
                            st.write(f"{risk_emoji} **{cls['position'].get('asset', 'unknown')}** — {cls.get('reasoning', 'No reasoning')}")
            except Exception:
                pass

    except Exception as e:
        st.error(f"Failed to load recovery alerts: {e}")
