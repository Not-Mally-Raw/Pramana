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
        # Fix 1: Decode hex calldata before regex matching.
        # Real-world calldata is 0x-prefixed hex — regex patterns need decoded text.
        raw_calldata = event.calldata or ""
        decoded_text = self._decode_calldata_for_scan(raw_calldata)
        
        # Phase 1: Native ABI Calldata Parsing
        # Extract explicit string parameters even if deeply nested in AA batch calls
        from core.parsers.calldata import decode_all_calls
        parsed_strings = []
        calls = decode_all_calls(raw_calldata)
        for c in calls:
            parsed_strings.extend(c.strings)
            
        text = decoded_text + " " + " ".join(parsed_strings) + " " + (event.action or "")

        meta: dict[str, Any] = {}
        matched_labels: list[str] = []
        max_score = 0.0

        for compiled, label, sev in _COMPILED:
            if compiled.search(text):
                matched_labels.append(label)
                max_score = max(max_score, sev)

        meta["matched_patterns"] = matched_labels
        meta["calldata_len"] = len(text)
        meta["calldata_decoded"] = decoded_text != raw_calldata

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
        """Check calldata for known dangerous 4-byte function selectors.

        Fix 2: Use proper 8-hex-char selectors (4 bytes). The old code used
        'ff' (2 chars) compared against cd[:8] (8 chars), which never matched.
        Fix 10 (partial): approve_erc20 removed — it's the most common DeFi
        call and generated massive false positives.
        """
        DANGEROUS = {
            # selfdestruct — padded to 4-byte selector format
            "ff000000": "selfdestruct",
            # delegatecall proxy patterns
            "f8dc5dd9": "delegatecall_proxy_upgrade",
            "3659cfe6": "upgradeTo",
            "4f1ef286": "upgradeToAndCall",
            # Ownership transfer (suspicious if unexpected)
            "f2fde38b": "transferOwnership",
            # Proxy admin change
            "8f283970": "changeAdmin",
        }
        cd = calldata.replace("0x", "").lower()
        found = []
        if len(cd) >= 8:
            selector = cd[:8]
            if selector in DANGEROUS:
                found.append(DANGEROUS[selector])
        return found

    @staticmethod
    def _decode_calldata_for_scan(calldata: str) -> str:
        """Decode hex calldata to extract scannable text content.

        Fix 1: Real-world calldata is 0x-prefixed ABI-encoded hex. Regex
        patterns for prompt injection match plaintext, not hex digits.
        This method hex-decodes the bytes, extracts any UTF-8 strings
        embedded in the ABI encoding, and returns them for regex scanning.
        """
        if not calldata or not calldata.startswith("0x"):
            return calldata  # Already plaintext or empty

        hex_str = calldata[2:]  # Strip 0x prefix
        from core.parsers.calldata import _safe_hex_bytes

        raw_bytes = _safe_hex_bytes(hex_str)
        if not raw_bytes:
            return calldata  # Invalid hex, return as-is

        # Strategy 1: Full UTF-8 decode (works if entire payload is text)
        try:
            decoded = raw_bytes.decode("utf-8", errors="ignore")
            # Filter to printable characters (ABI padding produces nulls)
            printable = "".join(c if c.isprintable() or c in "\n\r\t" else " " for c in decoded)
            # Collapse whitespace
            printable = re.sub(r"\s+", " ", printable).strip()
            if len(printable) > 10:  # Meaningful text found
                return printable
        except Exception:
            pass

        # Strategy 2: Extract ABI string parameters (offset-encoded UTF-8)
        # ABI strings have: 32-byte offset, 32-byte length, then UTF-8 bytes
        extracted_strings: list[str] = []
        i = 0
        while i + 64 <= len(raw_bytes):
            # Check if this 32-byte word looks like a string length (reasonable)
            try:
                length = int.from_bytes(raw_bytes[i + 32 : i + 64], "big")
                if 4 < length < 2048 and i + 64 + length <= len(raw_bytes):
                    candidate = raw_bytes[i + 64 : i + 64 + length]
                    text = candidate.decode("utf-8", errors="strict")
                    if text.isprintable() and len(text) > 4:
                        extracted_strings.append(text)
                        i += 64 + length
                        continue
            except (ValueError, UnicodeDecodeError):
                pass
            i += 32

        if extracted_strings:
            return " ".join(extracted_strings)

        return calldata  # Fallback: return original
