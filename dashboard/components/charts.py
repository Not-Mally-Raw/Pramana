from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st


def base_layout(title: str, y_title: str = "") -> dict:
    return {
        "title": {"text": title, "font": {"size": 15, "color": "#d7f6ff"}},
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "font": {"color": "#cbeef8"},
        "margin": {"l": 35, "r": 24, "t": 55, "b": 35},
        "hovermode": "x unified",
        "yaxis": {"title": y_title, "gridcolor": "rgba(120,180,200,0.2)", "zeroline": False},
        "xaxis": {"gridcolor": "rgba(120,180,200,0.1)"},
        "legend": {"orientation": "h", "y": 1.09, "x": 0},
    }


def status_dot(status: str) -> str:
    s = (status or "").lower()
    if s in {"active", "ok", "completed"}:
        return "<span style='color:#32d583'>●</span> Active"
    if s in {"paused", "failed", "error"}:
        return "<span style='color:#ff6b6b'>●</span> Paused"
    return "<span style='color:#f59e0b'>●</span> Degraded"


def make_stacked_area(df, x_col: str, series: list[tuple[str, str]]) -> go.Figure:
    fig = go.Figure()
    for col_name, color in series:
        fig.add_trace(
            go.Scatter(
                x=df[x_col],
                y=df[col_name],
                mode="lines",
                stackgroup="one",
                name=col_name.replace("_", " ").title(),
                line={"width": 1.8, "color": color},
            )
        )
    return fig


def export_controls(df, file_name: str) -> None:
    if df is None or getattr(df, "empty", True):
        return
    st.download_button(
        "Export CSV",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name=file_name,
        mime="text/csv",
    )
