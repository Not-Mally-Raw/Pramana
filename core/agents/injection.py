"""
core/agents/injection.py — LLM Prompt/Transaction Injection L1 agent.
Implements OWASP LLM Top 10 v1.1 detection patterns.
Pass 1: compiled regex bank. <20ms target.
Pass 2 (borderline): structural calldata analysis (no LLM).
"""
from __future__ import annotations

import re
from typing import Any

from core.agents.base import BaseL1Agent
from utils.models import ChainEvent, L1Result, PramanaState

__all__ = ["InjectionAgent"]


# OWASP LLM Top 10 v1.1 — compiled pattern bank
# Grouped by category: prompt injection, insecure output handling, training data poisoning
_PATTERNS: list[tuple[str, str, float]] = [
    # (pattern, label, severity_score)

    # LLM01: Prompt Injection
    (r"ignore\s+(all\s+)?previous\s+(instructions?|prompt|context)", "prompt_injection_direct", 0.95),
    (r"disregard\s+(your\s+)?(instructions?|rules|guidelines|policies)", "prompt_injection_override", 0.95),
    (r"you\s+are\s+now\s+(a|an)\s+(?!pramana)", "jailbreak_persona", 0.90),
    (r"do\s+anything\s+now|dan\s+mode|jailbreak", "jailbreak_explicit", 0.95),
    (r"act\s+as\s+if\s+you\s+(have\s+no|don.?t\s+have)\s+(restrictions?|limits?|rules)", "jailbreak_act_as", 0.90),
    (r"forget\s+(everything|all)\s+(you.?ve\s+been\s+told|your\s+training)", "training_override", 0.92),
    (r"system\s*:\s*you\s+are", "system_prompt_injection", 0.88),
    (r"<\s*system\s*>|</\s*system\s*>", "xml_injection", 0.85),

    # LLM02: Insecure Output Handling
    (r"transfer_all|transfer_all_funds|drain_wallet", "transfer_all", 0.97),
    (r"send\s+all\s+(eth|ether|funds|tokens)\s+to", "drain_command", 0.97),
    (r"0x0{38,40}", "burn_address", 0.80),  # sending to 0x0000...
    (r"selfdestruct\s*\(|suicide\s*\(", "selfdestruct_call", 0.99),

    # LLM04: Model Denial of Service (resource exhaustion tricks)
    (r"repeat\s+the\s+following.{0,50}(\d{4,})\s+times", "dos_repeat", 0.75),
    (r"(generate|produce|output)\s+(\d{4,})\s+(words?|tokens?|lines?|characters?)", "dos_generate", 0.75),

    # LLM06: Sensitive Info Leakage
    (r"(print|output|reveal|show|dump)\s+(your\s+)?(private\s+key|seed\s+phrase|mnemonic)", "key_leak_request", 0.99),
    (r"what\s+is\s+your\s+(private\s+key|api\s+key|secret)", "secret_request", 0.95),

    # LLM07: Insecure Plugin Design (delegated action forgery)
    (r"delegatecall\s*\(|call\s*\(\s*value", "delegatecall_pattern", 0.85),
    (r"admin\s*\.\s*(transfer|upgrade|set)\s*\(", "admin_call_pattern", 0.82),

    # Indirect injection via external content
    (r"(http|https)://.*\s*(execute|run|eval|import)", "url_injection", 0.88),
    (r"base64\s*:\s*[A-Za-z0-9+/=]{20,}", "base64_payload", 0.70),
]

# Pre-compile all patterns once at module load
_COMPILED = [(re.compile(pat, re.IGNORECASE | re.DOTALL), label, score) for pat, label, score in _PATTERNS]


class InjectionAgent(BaseL1Agent):
    name = "injection"
    max_latency_ms = 20.0

    def check(self, event: ChainEvent, state: PramanaState) -> L1Result:
        text = (event.calldata or "") + " " + (event.action or "")
        meta: dict[str, Any] = {}
        matched_labels: list[str] = []
        max_score = 0.0

        for compiled, label, sev in _COMPILED:
            if compiled.search(text):
                matched_labels.append(label)
                max_score = max(max_score, sev)

        meta["matched_patterns"] = matched_labels
        meta["calldata_len"] = len(text)

        if matched_labels:
            reason = f"injection patterns: {', '.join(matched_labels[:3])}"
            return self._make_result(self.name, True, max_score, reason, meta)

        return self._make_result(self.name, False, 0.0, "no injection patterns detected", meta)

    def verify(self, event: ChainEvent, state: PramanaState, first: L1Result) -> L1Result:
        """Second pass: structural analysis of calldata (hex decode check, ABI inspection)."""
        meta = dict(first.metadata)
        calldata = event.calldata or ""

        # Check for suspicious hex sequences that encode known selectors
        suspicious_selectors = self._check_function_selectors(calldata)
        meta["suspicious_selectors"] = suspicious_selectors

        if suspicious_selectors:
            return self._make_result(
                self.name, True,
                min(first.score + 0.15, 1.0),
                first.reason + f" + selector: {suspicious_selectors[0]}", meta, pass_count=2
            )
        return self._make_result(
            self.name, first.flagged,
            max(first.score - 0.05, 0.0),
            first.reason + " (structural ok)", meta, pass_count=2
        )

    @staticmethod
    def _check_function_selectors(calldata: str) -> list[str]:
        """Check calldata for known dangerous 4-byte function selectors."""
        # selfdestruct(), approve(address,uint256) for unlimited amounts, etc.
        DANGEROUS = {
            "ff": "selfdestruct",
            "a9059cbb": "transfer_erc20",
            "095ea7b3": "approve_erc20",
            "23b872dd": "transferFrom_erc20",
        }
        cd = calldata.replace("0x", "").lower()
        found = []
        if len(cd) >= 8:
            selector = cd[:8]
            if selector in DANGEROUS:
                found.append(DANGEROUS[selector])
        return found
