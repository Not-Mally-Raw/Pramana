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
        """First pass: ABI-aware calldata parsing and deep address validation."""
        meta: dict[str, Any] = {}
        issues: list[str] = []
        score = 0.0

        from core.parsers.calldata import decode_all_calls

        parsed_payload = self._parse_calldata(event.calldata or "")
        meta["payload_parsed"] = isinstance(parsed_payload, dict)

        # ── ABI Calldata Parsing ──────────────────────────────────────────
        calls = decode_all_calls(event.calldata or "")
        meta["decoded_calls_count"] = len(calls)

        if calls:
            meta["primary_protocol"] = calls[0].protocol
            meta["primary_action"] = calls[0].name

            # Check for explicitly risky selectors
            for c in calls:
                if c.risk == "critical":
                    issues.append(f"critical action: {c.name} ({c.protocol})")
                    score = max(score, 0.95)
                elif c.risk == "high":
                    issues.append(f"high risk action: {c.name} ({c.protocol})")
                    score = max(score, 0.80)
        else:
            # Fallback for unrecognized/opaque calldata
            if parsed_payload is None and event.calldata and len(event.calldata) > 10:
                issues.append("opaque/unrecognized calldata payload")
                score = max(score, 0.40)

        # ── JSON schema fallback for plain calldata payloads ──────────────
        normalized_action = (event.action or "").lower()
        schema = _SCHEMAS.get(normalized_action)
        if isinstance(parsed_payload, dict) and schema:
            missing = [
                field
                for field in schema.get("required", [])
                if field not in parsed_payload
            ]
            if missing:
                issues.append(f"missing required fields: {', '.join(missing)}")
                score = max(score, 0.85)

            for field, rules in schema.get("properties", {}).items():
                if field not in parsed_payload:
                    continue
                pattern = rules.get("pattern")
                value = str(parsed_payload.get(field, ""))
                if pattern:
                    import re

                    if not re.match(pattern, value):
                        issues.append(f"invalid {field} format")
                        score = max(score, 0.85)

        # ── Deep Address Validation ───────────────────────────────────────
        all_addresses = set()
        for c in calls:
            all_addresses.update(c.addresses)

        if isinstance(parsed_payload, dict):
            for key, value in parsed_payload.items():
                if not isinstance(value, str):
                    continue
                if key.lower() in {
                    "to",
                    "from",
                    "spender",
                    "token",
                    "token_in",
                    "token_out",
                    "recipient",
                    "receiver",
                    "owner",
                }:
                    all_addresses.add(value)

        invalid_addrs = [a for a in all_addresses if not self._is_valid_address(a)]
        if invalid_addrs:
            issues.append(f"invalid addresses found in calldata: {len(invalid_addrs)}")
            score = max(score, 0.85)
            meta["invalid_addresses"] = invalid_addrs

        # ── Value sanity check ────────────────────────────────────────────
        value_eth = event.value_wei / 1e18 if event.value_wei else 0.0
        meta["value_eth"] = round(value_eth, 6)
        if value_eth > 100:  # >100 ETH in single tx is suspicious
            issues.append(f"unusually large value: {value_eth:.2f} ETH")
            score = max(score, 0.65)

        flagged = score >= 0.50
        reason = "; ".join(issues) if issues else "validation passed"
        return self._make_result(self.name, flagged, score, reason, meta)

    def verify(
        self, event: ChainEvent, state: PramanaState, first: L1Result
    ) -> L1Result:
        """Second pass: eth_call dry-run simulation on Anvil."""
        meta = dict(first.metadata)
        s = get_settings()
        sim_ok, sim_error = self._eth_call_dry_run(event, s.ANVIL_RPC)
        meta["simulation_ok"] = sim_ok
        meta["simulation_error"] = sim_error

        if not sim_ok:
            return self._make_result(
                self.name,
                True,
                min(first.score + 0.20, 1.0),
                first.reason + f" (sim failed: {sim_error[:60]})",
                meta,
                pass_count=2,
            )
        return self._make_result(
            self.name,
            first.flagged,
            max(first.score - 0.10, 0.0),
            first.reason + " (simulation passed)",
            meta,
            pass_count=2,
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
            from core.parsers.calldata import _safe_hex_bytes

            try:
                raw = _safe_hex_bytes(calldata[2:])
                if not raw:
                    return None
                decoded = raw.decode("utf-8", errors="ignore")
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
                        "from": (
                            event.agent_id
                            if event.agent_id.startswith("0x")
                            else "0x0000000000000000000000000000000000000001"
                        ),
                        "to": (
                            event.agent_id
                            if event.agent_id.startswith("0x")
                            else "0x0000000000000000000000000000000000000001"
                        ),
                        "data": (
                            event.calldata
                            if event.calldata and event.calldata.startswith("0x")
                            else "0x"
                        ),
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
