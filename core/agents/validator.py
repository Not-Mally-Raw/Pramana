"""
core/agents/validator.py — Schema + dry-run transaction validator L1 agent.
Pass 1: JSON schema validation of action/calldata. <50ms.
Pass 2 (borderline): eth_call dry-run on Anvil (simulation, no state change).
"""
from __future__ import annotations

import json
from typing import Any

import requests

from core.agents.base import BaseL1Agent
from core.config.settings import get_settings
from utils.models import ChainEvent, L1Result, PramanaState

__all__ = ["ValidatorAgent"]

# Minimal JSON schema for known action types
_SCHEMAS: dict[str, dict] = {
    "transfer": {
        "required": ["to", "value"],
        "properties": {
            "to": {"type": "string", "pattern": "^0x[0-9a-fA-F]{40}$"},
            "value": {"type": ["string", "number"]},
        },
    },
    "approve": {
        "required": ["spender", "amount"],
        "properties": {
            "spender": {"type": "string", "pattern": "^0x[0-9a-fA-F]{40}$"},
            "amount": {"type": ["string", "number"]},
        },
    },
    "swap": {
        "required": ["token_in", "token_out", "amount_in"],
    },
}


class ValidatorAgent(BaseL1Agent):
    name = "validator"
    max_latency_ms = 50.0

    def check(self, event: ChainEvent, state: PramanaState) -> L1Result:
        meta: dict[str, Any] = {}
        issues: list[str] = []
        score = 0.0

        # ── JSON schema validation ─────────────────────────────────────────
        calldata_obj = self._parse_calldata(event.calldata or "")
        meta["calldata_parsed"] = bool(calldata_obj)

        action_lower = (event.action or "").lower()

        if calldata_obj and action_lower in _SCHEMAS:
            schema = _SCHEMAS[action_lower]
            missing = [k for k in schema.get("required", []) if k not in calldata_obj]
            if missing:
                issues.append(f"schema violation: missing required fields {missing}")
                score = max(score, 0.70)
                meta["schema_missing_fields"] = missing

            # Property pattern validation
            for field, rule in schema.get("properties", {}).items():
                if field in calldata_obj and "pattern" in rule:
                    if not isinstance(calldata_obj[field], str) or not __import__("re").match(
                        rule["pattern"], calldata_obj[field]
                    ):
                        issues.append(f"field '{field}' format invalid")
                        score = max(score, 0.65)

        # ── Address validation ────────────────────────────────────────────
        if calldata_obj:
            for field in ("to", "from", "spender", "recipient"):
                addr = calldata_obj.get(field, "")
                if addr and not self._is_valid_address(addr):
                    issues.append(f"invalid address in '{field}': {str(addr)[:20]}")
                    score = max(score, 0.75)

        # ── Value sanity check ────────────────────────────────────────────
        value_eth = event.value_wei / 1e18 if event.value_wei else 0.0
        meta["value_eth"] = round(value_eth, 6)
        if value_eth > 100:  # >100 ETH in single tx is suspicious
            issues.append(f"unusually large value: {value_eth:.2f} ETH")
            score = max(score, 0.65)

        flagged = score >= 0.50
        reason = "; ".join(issues) if issues else "validation passed"
        return self._make_result(self.name, flagged, score, reason, meta)

    def verify(self, event: ChainEvent, state: PramanaState, first: L1Result) -> L1Result:
        """Second pass: eth_call dry-run simulation on Anvil."""
        meta = dict(first.metadata)
        s = get_settings()
        sim_ok, sim_error = self._eth_call_dry_run(event, s.ANVIL_RPC)
        meta["simulation_ok"] = sim_ok
        meta["simulation_error"] = sim_error

        if not sim_ok:
            return self._make_result(
                self.name, True, min(first.score + 0.20, 1.0),
                first.reason + f" (sim failed: {sim_error[:60]})", meta, pass_count=2
            )
        return self._make_result(
            self.name, first.flagged, max(first.score - 0.10, 0.0),
            first.reason + " (simulation passed)", meta, pass_count=2
        )

    # ── Internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_calldata(calldata: str) -> dict | None:
        if not calldata:
            return None
        # Try JSON first, then hex-encoded JSON
        try:
            return json.loads(calldata)
        except (json.JSONDecodeError, ValueError):
            pass
        if calldata.startswith("0x"):
            try:
                decoded = bytes.fromhex(calldata[2:]).decode("utf-8", errors="ignore")
                return json.loads(decoded)
            except Exception:
                pass
        return None

    @staticmethod
    def _is_valid_address(addr: str) -> bool:
        import re
        return bool(re.match(r"^0x[0-9a-fA-F]{40}$", str(addr)))

    @staticmethod
    def _eth_call_dry_run(event: ChainEvent, rpc_url: str) -> tuple[bool, str]:
        """Simulate transaction via eth_call. No state change."""
        try:
            payload = {
                "jsonrpc": "2.0",
                "method": "eth_call",
                "params": [
                    {
                        "from": event.agent_id if event.agent_id.startswith("0x") else "0x0000000000000000000000000000000000000001",
                        "to":   event.agent_id if event.agent_id.startswith("0x") else "0x0000000000000000000000000000000000000001",
                        "data": event.calldata if event.calldata and event.calldata.startswith("0x") else "0x",
                        "value": hex(event.value_wei) if event.value_wei else "0x0",
                    },
                    "latest",
                ],
                "id": 99,
            }
            resp = requests.post(rpc_url, json=payload, timeout=3)
            if resp.status_code != 200:
                return False, f"HTTP {resp.status_code}"
            result = resp.json()
            if "error" in result:
                return False, result["error"].get("message", "eth_call error")
            return True, ""
        except Exception as e:
            return False, str(e)[:80]
