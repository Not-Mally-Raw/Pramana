"""
dashboard/pages/policies.py — NL policy editor.
User writes a natural language guard rule → Groq parses to JSON → persisted to SQLite.
"""
from __future__ import annotations

import json
import requests
import streamlit as st


_POLICY_PARSE_PROMPT = """
You are a blockchain AI-agent policy parser.
Convert the user's natural language rule into a structured JSON guard rule.

Return ONLY a valid JSON array of rule objects. Each object must have:
{
  "id": "<short slug>",
  "type": "allow" | "deny",
  "condition": "<what triggers the rule>",
  "action": "block" | "flag" | "escalate" | "allow",
  "severity": 1-10,
  "description": "<original text (max 100 chars)>"
}

Example input: "Block any transfer over 10 ETH"
Example output: [{"id":"max_transfer","type":"deny","condition":"value_eth > 10","action":"block","severity":9,"description":"Block any transfer over 10 ETH"}]
""".strip()


def render(api_url: str, org_id: str) -> None:
    st.header("Policy Editor")
    st.caption("Write guard rules in plain English. Groq parses them to structured policies stored on-chain.")

    # ── NL Policy Parser ──────────────────────────────────────────────────────
    st.subheader("Create New Policy")
    with st.form("policy_form"):
        policy_id  = st.text_input("Policy ID", "policy_001")
        agent_addr = st.text_input("Agent Address", "0x0000000000000000000000000000000000000001")
        nl_input   = st.text_area(
            "Describe your guard rule(s)",
            "Block any transaction that sends more than 1 ETH in a single call.\n"
            "Flag any action that repeats more than 3 times per minute.",
            height=100,
        )
        use_groq  = st.checkbox("Parse with Groq LLM", value=True)
        submitted = st.form_submit_button("Create Policy")

    if submitted:
        if use_groq:
            rules = _parse_with_groq(nl_input)
        else:
            # Fallback: wrap text as single raw rule
            rules = [{"id": "raw", "type": "deny", "condition": nl_input, "action": "flag",
                      "severity": 5, "description": nl_input[:100]}]

        if rules:
            st.json(rules)
            try:
                resp = requests.post(
                    f"{api_url}/policies",
                    json={"policy_id": policy_id, "agent_addr": agent_addr, "rules": rules},
                    headers={"X-Org-Id": org_id},
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()["data"]
                    st.success(f"Policy created! Hash: `{data['rule_hash'][:16]}…`")
                else:
                    st.error(f"API error: {resp.text[:200]}")
            except Exception as e:
                st.error(f"Failed to save policy: {e}")
        else:
            st.error("Could not parse rules. Check Groq API key in .env")

    st.divider()

    # ── Existing policies ─────────────────────────────────────────────────────
    st.subheader("Active Policies")
    agent_filter = st.text_input("Filter by agent address", "")
    if st.button("Load Policies") or agent_filter:
        addr = agent_filter.strip() or "0x0000000000000000000000000000000000000001"
        try:
            resp = requests.get(
                f"{api_url}/policies/{addr}",
                headers={"X-Org-Id": org_id},
                timeout=5,
            )
            policies = resp.json().get("data", []) if resp.status_code == 200 else []
            if policies:
                for p in policies:
                    with st.expander(f"v{p['version']} — {p['policy_id']} (hash: {p['rule_hash'][:12]}…)"):
                        try:
                            st.json(json.loads(p["rules_json"]))
                        except Exception:
                            st.code(p["rules_json"])
            else:
                st.info("No policies found for this agent.")
        except Exception as e:
            st.error(f"Failed to load policies: {e}")


def _parse_with_groq(nl_text: str) -> list[dict]:
    try:
        from groq import Groq
        import os
        from core.config.settings import get_settings
        s = get_settings()
        if not s.GROQ_API_KEY:
            st.warning("GROQ_API_KEY not set. Using raw rule fallback.")
            return []

        from utils.cache import can_call_groq, record_groq_call
        if not can_call_groq():
            st.warning("Groq rate limit reached. Try again shortly.")
            return []

        client = Groq(api_key=s.GROQ_API_KEY)
        record_groq_call()
        completion = client.chat.completions.create(
            model=s.GROQ_MODEL_L1,
            messages=[
                {"role": "system", "content": _POLICY_PARSE_PROMPT},
                {"role": "user",   "content": nl_text},
            ],
            temperature=0.0,
            max_tokens=512,
        )
        raw = completion.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw)
    except Exception as e:
        st.error(f"Groq parse failed: {e}")
        return []
