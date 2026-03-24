"""
core/agents/sanctions.py — Real-time OFAC/AML Sanctions Screening Agent

Uses CalldataParser to extract all deeply nested addresses from an 
Agent's proposed transaction, checking them against a local SQLite 
replica of the OFAC SDN list. Any match results in an immediate 1.0 risk score.
"""
from typing import Any
from utils.models import ChainEvent, L1Result, PramanaState
from core.agents.base import BaseL1Agent
from core.parsers.calldata import decode_all_calls
from core.db import is_sanctioned

__all__ = ["SanctionsAgent"]

class SanctionsAgent(BaseL1Agent):
    name = "sanctions"
    max_latency_ms = 15.0  # Extremely fast SQLite primary key lookup
    
    # We do not do a second pass for sanctions. It's binary.
    borderline_low = 2.0
    borderline_high = 2.0

    def check(self, event: ChainEvent, state: PramanaState) -> L1Result:
        meta: dict[str, Any] = {}
        issues: list[str] = []
        score = 0.0

        # 1. Check the agent's own address and the direct 'to' address
        addresses_to_check = set()
        if event.agent_id:
            addresses_to_check.add(event.agent_id.lower())
        
        # 2. Extract deep addresses using ABI CalldataParser
        calls = decode_all_calls(event.calldata or "")
        meta["decoded_calls"] = len(calls)
        
        for c in calls:
            for addr in c.addresses:
                if addr:
                    addresses_to_check.add(addr.lower())
                    
        meta["unique_addresses_screened"] = len(addresses_to_check)

        # 3. Screen against local OFAC SQLite replica
        sanctioned_hits = []
        for addr in addresses_to_check:
            if is_sanctioned(addr):
                sanctioned_hits.append(addr)
                
        if sanctioned_hits:
            issues.append(f"SANCTIONS HIT: {len(sanctioned_hits)} addresses on OFAC list")
            meta["sanctioned_addresses"] = sanctioned_hits
            score = 1.0  # Hard stop

        flagged = score >= 0.50
        reason = "; ".join(issues) if issues else "aml_clear"
        return self._make_result(self.name, flagged, score, reason, meta)
