"""
dashboard/views/lifecycle.py — Agent Lifecycle State Visualization
Shows agent registration, active state, degradation, and pause transitions.
"""
from __future__ import annotations

import streamlit as st
import pandas as pd
from core.db import get_sqlite_conn


@st.cache_data(ttl=10)
def _fetch_lifecycle_data():
    """Fetch latest lifecycle state per agent."""
    with get_sqlite_conn() as conn:
        rows = conn.execute("""
            SELECT lt.agent_addr, lt.to_state, lt.from_state, 
                   lt.reason, lt.created_at
            FROM lifecycle_transitions lt
            INNER JOIN (
                SELECT agent_addr, MAX(created_at) as max_ts
                FROM lifecycle_transitions
                GROUP BY agent_addr
            ) latest ON lt.agent_addr = latest.agent_addr 
                     AND lt.created_at = latest.max_ts
            ORDER BY lt.created_at DESC
        """).fetchall()
    return rows


STATE_COLORS = {
    "ACTIVE": "🟢",
    "DEGRADED": "🟡", 
    "PAUSED": "🔴",
    "RECOVERING": "🟠",
    "REGISTERED": "⚪",
    "SUSPENDED": "⛔",
    "RETIRED": "⬛"
}


def render():
    """Render agent lifecycle states."""
    st.header("Agent Lifecycle States")
    
    rows = _fetch_lifecycle_data()
    
    if not rows:
        st.info("No agents have recorded lifecycle transitions yet.")
        st.markdown(
            "Agents transition through states as Pramana monitors them: "
            "**REGISTERED** → **ACTIVE** → **DEGRADED** → **PAUSED** → **RECOVERING** → **ACTIVE**"
        )
        return
    
    st.markdown("#### Current Agent States")
    
    # Summary metrics
    c1, c2, c3 = st.columns(3)
    active_count = sum(1 for r in rows if r[1] == "ACTIVE")
    paused_count = sum(1 for r in rows if r[1] == "PAUSED")
    degraded_count = sum(1 for r in rows if r[1] == "DEGRADED")
    
    c1.metric("Active Agents", active_count)
    c2.metric("Paused Agents", paused_count)
    c3.metric("Degraded Agents", degraded_count)
    
    st.divider()
    
    # Detailed per-agent view
    st.markdown("#### Agent Details")
    for row in rows:
        agent_addr, to_state, from_state, reason, created_at = row
        icon = STATE_COLORS.get(to_state, "❓")
        
        with st.expander(f"{icon} {agent_addr[:10]}...{agent_addr[-6:]} — {to_state}"):
            col1, col2 = st.columns(2)
            with col1:
                st.metric("Current State", to_state)
                st.metric("Previous State", from_state or "—")
            with col2:
                ts_str = pd.Timestamp(created_at, unit='s').strftime('%Y-%m-%d %H:%M:%S') if created_at else "—"
                st.metric("Transition Time", ts_str)
            if reason:
                st.caption(f"**Reason:** {reason}")
    
    st.divider()
    st.markdown(
        "### Lifecycle Diagram\n"
        "```\n"
        "┌──────────┐\n"
        "│REGISTERED├──→ ACTIVE ──→ DEGRADED ──→ PAUSED ──→ RECOVERING ───┐\n"
        "│ (new)    │                      ↑                              │\n"
        "└──────────┘                      │                              │\n"
        "                            (recovery alert)              (success)\n"
        "                                   ↓                              │\n"
        "                            ┌──────────┐←─────────────────────────┘\n"
        "                            │  ACTIVE  │\n"
        "                            └──────────┘\n"
        "```"
    )
