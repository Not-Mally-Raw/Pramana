from __future__ import annotations

from datetime import datetime

import streamlit as st


def apply_theme() -> None:
    st.set_page_config(layout="wide", page_title="Pramana Monitor", page_icon="PM")
    st.markdown(
        """
        <style>
        :root {
          --bg-main: #071426;
          --bg-card: rgba(14, 35, 58, 0.75);
          --bg-card-soft: rgba(13, 26, 45, 0.7);
          --text-main: #d7f6ff;
          --text-muted: #87c6d9;
          --accent: #00e7ff;
          --accent-soft: #08a8bd;
          --ok: #32d583;
          --warn: #f59e0b;
          --err: #ff6b6b;
        }

        .stApp {
          background:
            radial-gradient(90rem 60rem at 6% -5%, rgba(0, 231, 255, 0.16), transparent 55%),
            radial-gradient(70rem 40rem at 95% 10%, rgba(20, 70, 110, 0.45), transparent 60%),
            linear-gradient(170deg, #051021 0%, #091a2f 40%, #071426 100%);
          color: var(--text-main);
        }

        [data-testid="stMetric"] {
          background: var(--bg-card);
          border: 1px solid rgba(0, 231, 255, 0.22);
          border-radius: 16px;
          padding: 12px 14px;
          backdrop-filter: blur(8px);
          box-shadow: 0 0 0 1px rgba(0, 180, 210, 0.1), 0 10px 30px rgba(4, 13, 25, 0.45);
        }

        .pm-card {
          background: var(--bg-card-soft);
          border: 1px solid rgba(0, 231, 255, 0.2);
          border-radius: 14px;
          padding: 12px 14px;
          margin-bottom: 10px;
        }

        .pm-badge {
          padding: 3px 8px;
          border-radius: 999px;
          border: 1px solid rgba(0, 231, 255, 0.3);
          font-size: 12px;
          display: inline-block;
          margin-right: 8px;
        }

        .pm-footer {
          margin-top: 18px;
          color: var(--text-muted);
          font-size: 12px;
          opacity: 0.95;
        }

        .pm-logo {
          font-weight: 700;
          color: var(--accent);
          letter-spacing: 0.5px;
        }

        .stTabs [data-baseweb="tab-list"] {
          gap: 10px;
        }

        .stTabs [data-baseweb="tab"] {
          background: rgba(8, 30, 52, 0.6);
          border-radius: 10px;
          border: 1px solid rgba(0, 231, 255, 0.15);
          padding: 8px 14px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def footer(version: str) -> None:
    year = datetime.utcnow().year
    st.markdown(
        (
            '<div class="pm-footer">'
            '<span class="pm-logo">Pramana</span> '
            f'| Monitor v{version} | {year} | On-chain AgentOps'
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def schedule_rerun(seconds: int = 15) -> None:
    # Use Streamlit native autorefresh if present; otherwise use a tiny browser timer fallback.
    if hasattr(st, "autorefresh"):
        st.autorefresh(interval=seconds * 1000, key=f"pm-refresh-{seconds}")
        return
    # On older Streamlit versions, no-op is safer than forcing full-page reloads.
    return
