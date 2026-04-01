"""
core/attestation.py — HMAC-SHA256 Attestation Service for AA Intercepts.
Provides cryptographic proof that Pramana evaluated a specific UserOperation.
"""
from __future__ import annotations

import hmac
import hashlib
import time
from typing import Optional

from core.db import get_sqlite_conn
from core.config.settings import get_settings


class AttestationReplayError(Exception):
    pass

class AttestationExpiredError(Exception):
    pass

class AttestationInvalidSignatureError(Exception):
    pass


class AttestationService:
    @staticmethod
    def get_current_block() -> Optional[int]:
        """Fetch current block number from active RPC, if available."""
        try:
            from web3 import Web3

            s = get_settings()
            w3 = Web3(Web3.HTTPProvider(s.active_rpc, request_kwargs={"timeout": 2}))
            if not w3.is_connected():
                return None
            return int(w3.eth.block_number)
        except Exception:
            return None

    @staticmethod
    def sign(user_op_hash: str, decision: str, risk_score: float, trace_hash: str, bundler_id: str, ttl_blocks: int = 5) -> dict:
        """
        Generates an HMAC-SHA256 signature binding the decision to the specific UserOperation.
        Logs the attestation to SQLite for replay protection.
        """
        s = get_settings()
        secret = s.ATTESTATION_HMAC_SECRET.encode('utf-8')

        current_block = AttestationService.get_current_block()
        expires_at_block = (current_block + ttl_blocks) if current_block is not None else 0
        expires_at = int(time.time()) + 60  # legacy fallback for payload compatibility
        
        # We need a unique nonce for the attestation itself
        # user_op_hash acts as the primary idempotency key.
        from utils.crypto import keccak256_hex
        attestation_nonce = keccak256_hex(f"{user_op_hash}:{expires_at_block}:{trace_hash}")

        payload = {
            "user_op_hash": user_op_hash,
            "decision": decision,
            "risk_score": risk_score,
            "trace_hash": trace_hash,
            "bundler_id": bundler_id,
            "expires_at_block": expires_at_block,
            "expires_at": expires_at,
            "nonce": attestation_nonce
        }
        
        # Deterministic HMAC Input (Strict Ordering, block-aware expiry).
        hmac_string = f"{user_op_hash}:{decision}:{risk_score}:{trace_hash}:{bundler_id}:{attestation_nonce}:{expires_at_block}"
        payload_bytes = hmac_string.encode('utf-8')
        
        signature = hmac.new(secret, payload_bytes, hashlib.sha256).hexdigest()
        
        # Log to DB for replay protection
        with get_sqlite_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO attestation_log
                  (user_op_hash, attestation_nonce, nonce, decision, risk_score, trace_hash, bundler_id,
                   redeemed, expires_at, expires_at_block, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, datetime('now'))
                """,
                (
                    user_op_hash,
                    attestation_nonce,
                    attestation_nonce,
                    decision,
                    float(risk_score),
                    trace_hash,
                    bundler_id,
                    expires_at,
                    expires_at_block,
                ),
            )
            
        return {
            "payload": payload,
            "signature": f"v1=hmac-sha256:{signature}"
        }

    @staticmethod
    def verify(user_op_hash: str, payload: dict, signature_header: str) -> bool:
        """
        Verifies signatures and TTL.
        """
        s = get_settings()
        secret = s.ATTESTATION_HMAC_SECRET.encode('utf-8')
        
        if not signature_header.startswith("v1=hmac-sha256:"):
            raise AttestationInvalidSignatureError("Unsupported signature version")
            
        provided_sig = signature_header.split(":")[1]

        # Verify TTL using block-based expiration when available.
        expires_at_block = int(payload.get("expires_at_block", 0) or 0)
        if expires_at_block > 0:
            current_block = AttestationService.get_current_block()
            if current_block is None or current_block > expires_at_block:
                raise AttestationExpiredError("Attestation has expired (block TTL)")
        else:
            # Legacy fallback for old payloads.
            if payload.get("expires_at", 0) < time.time():
                raise AttestationExpiredError("Attestation has expired")
            
        # Verify binding
        if payload.get("user_op_hash") != user_op_hash:
            raise AttestationInvalidSignatureError("Attestation bound to different user_op_hash")
            
        # Reconstruct strict deterministic HMAC
        try:
            decision = payload.get("decision", "")
            risk_score = payload.get("risk_score", 0.0)
            trace_hash = payload.get("trace_hash", "")
            bundler_id = payload.get("bundler_id", "")
            attestation_nonce = payload.get("nonce", "")
            expires_at_block = int(payload.get("expires_at_block", 0) or 0)
            
            hmac_string = f"{user_op_hash}:{decision}:{risk_score}:{trace_hash}:{bundler_id}:{attestation_nonce}:{expires_at_block}"
            payload_bytes = hmac_string.encode('utf-8')
            expected_sig = hmac.new(secret, payload_bytes, hashlib.sha256).hexdigest()
            
            if not hmac.compare_digest(expected_sig, provided_sig):
                raise AttestationInvalidSignatureError("Cryptographic signature mismatch")
        except Exception as e:
            raise AttestationInvalidSignatureError(f"Malformed HMAC verification string: {e}")
            
        return True

    @staticmethod
    def check_and_redeem_replay(user_op_hash: str) -> None:
        """
        Called EXACTLY ONCE when a validate request arrives. 
        If it's already in the log as redeemed, or we want to enforce strictly 1 eval per hash:
        We mark it redeemed. If already redeemed, throw ReplayError.
        """
        with get_sqlite_conn() as conn:
            # Check existence and redeemed status
            row = conn.execute(
                "SELECT redeemed, expires_at_block FROM attestation_log WHERE user_op_hash=?", 
                (user_op_hash,)
            ).fetchone()
            
            if row:
                if row["redeemed"]:
                    raise AttestationReplayError(f"UserOpHash {user_op_hash} already evaluated and redeemed")

            if row and row["redeemed"]:
                raise AttestationReplayError(f"Replay Attempt: {user_op_hash}")

    @staticmethod
    def mark_redeemed(user_op_hash: str) -> None:
        """Marks an attestation as fully consumed to finalize replay protection."""
        with get_sqlite_conn() as conn:
            conn.execute(
                "UPDATE attestation_log SET redeemed=1 WHERE user_op_hash=?",
                (user_op_hash,)
            )

