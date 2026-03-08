"""
dashboard/app.py — Main Streamlit dashboard entry point.
Sidebar navigation + org selector + health ticker.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st

st.set_page_config(
    page_title="Pramana AgentOps",
    page_icon="⛓️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("⛓️ Pramana")
    st.caption("Governing the Unseen")
    st.divider()

    org_id = st.text_input("Org ID", value="default", help="Your organization identifier")
    api_url = st.text_input("API URL", value="http://localhost:8000", help="Pramana API endpoint")

    st.divider()
    page = st.radio(
        "Navigation",
        ["Overview", "Policies", "Replay", "Identity"],
        label_visibility="collapsed",
    )

    st.divider()
    # Health ticker
    health_col1, health_col2 = st.columns(2)
    try:
        import requests
        resp = requests.get(f"{api_url}/health", timeout=2)
        if resp.status_code == 200:
            health_col1.success("API ✓")
        else:
            health_col1.error("API ✗")
    except Exception:
        health_col1.error("API ✗")

    try:
        import requests
        resp = requests.post(
            "http://127.0.0.1:8545",
            json={"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1},
            timeout=1,
        )
        health_col2.success("Chain ✓")
    except Exception:
        health_col2.warning("Chain ✗")

# Store in session for pages
st.session_state["org_id"]  = org_id
st.session_state["api_url"] = api_url

# ── Page routing ──────────────────────────────────────────────────────────────

if page == "Overview":
    from dashboard.pages import overview
    overview.render(api_url, org_id)
elif page == "Policies":
    from dashboard.pages import policies
    policies.render(api_url, org_id)
elif page == "Replay":
    from dashboard.pages import replay
    replay.render(api_url, org_id)
elif page == "Identity":
    from dashboard.pages import identity
    identity.render(api_url, org_id)
