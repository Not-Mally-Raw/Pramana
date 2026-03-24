from __future__ import annotations

from typing import Iterable

import streamlit as st

from core.config.settings import get_settings


def _build_context_snippet(page_name: str, context_rows: Iterable[dict], max_rows: int = 8) -> str:
    rows = list(context_rows)[:max_rows]
    parts = [f"Page: {page_name}"]
    for row in rows:
        parts.append(str(row))
    return "\n".join(parts)


def render_ask_ai(page_name: str, context_rows: Iterable[dict]) -> None:
    st.markdown("### Ask AI")
    st.caption("Query current page data and recent platform signals.")

    prompt = st.text_area(
        "Question",
        placeholder="Why is trading-bot-01 failing?",
        height=90,
        key=f"ask-ai-prompt-{page_name}",
    )

    if st.button("Ask", key=f"ask-ai-btn-{page_name}"):
        if not prompt.strip():
            st.warning("Enter a question first.")
            return

        settings = get_settings()
        if not settings.GROQ_API_KEY:
            st.info("GROQ_API_KEY not configured. Ask AI requires Groq access.")
            return

        context_blob = _build_context_snippet(page_name, context_rows)
        sys_prompt = (
            "You are the Pramana Monitor assistant. "
            "Answer using only provided dashboard context. "
            "If the answer is uncertain, state what additional telemetry is needed."
        )

        try:
            from groq import Groq

            client = Groq(api_key=settings.GROQ_API_KEY)
            completion = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                temperature=0.1,
                max_tokens=500,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {
                        "role": "user",
                        "content": (
                            f"Question: {prompt}\n\n"
                            "Dashboard context:\n"
                            f"{context_blob}"
                        ),
                    },
                ],
            )
            answer = (completion.choices[0].message.content or "").strip()
            st.success("AI analysis")
            st.write(answer or "No answer returned.")
        except Exception as exc:
            st.error(f"Ask AI failed: {exc}")
