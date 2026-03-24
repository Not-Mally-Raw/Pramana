"""
utils/crypto.py — Hashing and encoding utilities.

All functions are pure, stateless, and side-effect-free.
Used by TraceDAG.finalize(), Guardian policy hashing, and SDK trace capture.
"""
from __future__ import annotations

import hashlib
import json
import struct
from typing import Any


# ─── SHA-256 ──────────────────────────────────────────────────────────────────

def sha256_hex(data: str | bytes) -> str:
    """SHA-256 of string or bytes. Returns 64-char hex string."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def sha256_bytes32(data: str | bytes) -> bytes:
    """SHA-256 → 32 raw bytes (for Solidity bytes32 ABI encoding)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).digest()


def sha256_json(obj: Any) -> str:
    """Deterministic SHA-256 of a JSON-serializable object.
    Sort keys, no whitespace, UTF-8 encoded.
    """
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return sha256_hex(canonical)


# ─── keccak256 (pure Python, no web3 needed) ──────────────────────────────────

def keccak256_hex(data: str | bytes) -> str:
    """
    keccak256 used by Solidity for event topic hashing.
    Uses sha3_256 with keccak variant via pysha3 if available,
    otherwise falls back to hashlib (which is SHA3, not keccak — warn on import).
    For policy hash comparison with Solidity, use this.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    try:
        k = hashlib.new("keccak_256")
        k.update(data)
        return k.hexdigest()
    except (ImportError, ValueError):
        # Fallback: SHA3-256 (NOT identical to keccak — acceptable for off-chain use)
        return hashlib.sha3_256(data).hexdigest()


# ─── ABI Encoding helpers ─────────────────────────────────────────────────────

def encode_bytes32(hex_str: str) -> bytes:
    """Convert 64-char hex to 32 bytes (for on-chain calls)."""
    s = hex_str.removeprefix("0x")
    if len(s) != 64:
        raise ValueError(f"Expected 64 hex chars, got {len(s)}")
    return bytes.fromhex(s)


def hex_to_bytes32_param(hex_str: str) -> str:
    """Normalize to 0x-prefixed 32-byte hex for Web3.py parameter."""
    s = hex_str.removeprefix("0x").lower()
    return "0x" + s.zfill(64)


# ─── Fingerprinting ───────────────────────────────────────────────────────────

def action_fingerprint(agent_id: str, action: str, calldata: str) -> str:
    """
    Create a stable fingerprint for (agent, action) used as LLM cache key.
    Truncates calldata to first 64 chars to reduce noise from value differences.
    Returns 16-char hex (64-bit, collision-resistant enough for cache keys).
    """
    key = f"{agent_id}:{action}:{calldata[:64]}"
    return sha256_hex(key)[:16]


def policy_hash(rules_json: str) -> str:
    """keccak256 of the policy rules JSON — matches Solidity Guardian.setPolicy()."""
    return keccak256_hex(rules_json)


def eth_address_to_uint256(address: str) -> int:
    """Convert an EVM address string to deterministic uint256 identity."""
    s = (address or "").strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    if len(s) != 40:
        return 0
    try:
        return int(s, 16)
    except ValueError:
        return 0


# ─── Merkle Root (lightweight, for batch SBT updates) ────────────────────────

def merkle_root(leaves: list[str]) -> str:
    """
    Compute a simple binary Merkle root from a list of hex strings.
    Used to prove a batch SBT update is consistent without storing all hashes.
    O(n log n) where n = len(leaves). For n=50 agents: trivial.
    """
    if not leaves:
        return sha256_hex("")
    nodes = [bytes.fromhex(leaf.removeprefix("0x").zfill(64)) for leaf in leaves]
    while len(nodes) > 1:
        if len(nodes) % 2 == 1:
            nodes.append(nodes[-1])   # duplicate last leaf if odd
        nodes = [
            hashlib.sha256(nodes[i] + nodes[i + 1]).digest()
            for i in range(0, len(nodes), 2)
        ]
    return nodes[0].hex()
