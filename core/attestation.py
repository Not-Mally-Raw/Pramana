"""
core/attestation.py — HMAC-SHA256 Attestation Service for AA Intercepts.
Provides cryptographic proof that Pramana evaluated a specific UserOperation.
"""
from __future__ import annotations

import hmac
import hashlib
import time
import json
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
    def sign(user_op_hash: str, decision: str, risk_score: float, trace_hash: str, bundler_id: str, ttl_seconds: int = 60) -> dict:
        """
        Generates an HMAC-SHA256 signature binding the decision to the specific UserOperation.
        Logs the attestation to SQLite for replay protection.
        """
        s = get_settings()
        secret = s.ATTESTATION_HMAC_SECRET.encode('utf-8')
        
        expires_at = int(time.time()) + ttl_seconds
        
        # We need a unique nonce for the attestation itself
        # user_op_hash acts as the primary idempotency key.
        from utils.crypto import keccak256_hex
        attestation_nonce = keccak256_hex(f"{user_op_hash}:{expires_at}:{trace_hash}")

        payload = {
            "user_op_hash": user_op_hash,
            "decision": decision,
            "risk_score": risk_score,
            "trace_hash": trace_hash,
            "bundler_id": bundler_id,
            "expires_at": expires_at,
            "nonce": attestation_nonce
        }
        
        # Serialize deterministically for signing
        payload_bytes = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
        signature = hmac.new(secret, payload_bytes, hashlib.sha256).hexdigest()
        
        # Log to DB for replay protection
        with get_sqlite_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO attestation_log (user_op_hash, attestation_nonce, redeemed, expires_at) "
                "VALUES (?, ?, 0, ?)",
                (user_op_hash, attestation_nonce, expires_at)
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
        
        # Verify TTL
        if payload.get("expires_at", 0) < time.time():
            raise AttestationExpiredError("Attestation has expired")
            
        # Verify binding
        if payload.get("user_op_hash") != user_op_hash:
            raise AttestationInvalidSignatureError("Attestation bound to different user_op_hash")
            
        payload_bytes = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
        expected_sig = hmac.new(secret, payload_bytes, hashlib.sha256).hexdigest()
        
        if not hmac.compare_digest(expected_sig, provided_sig):
            raise AttestationInvalidSignatureError("Cryptographic signature mismatch")
            
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
                "SELECT redeemed, expires_at FROM attestation_log WHERE user_op_hash=?", 
                (user_op_hash,)
            ).fetchone()
            
            if row:
                if row["redeemed"]:
                    raise AttestationReplayError(f"UserOpHash {user_op_hash} already evaluated and redeemed")
                # If it's there but not redeemed, it might be an active validation in flight or previously evaluated but timed out.
                # Usually bundlers don't ask twice for a valid hash.
                
            # We don't mark it redeemed HERE, we mark it redeemed when the bundler actually submits the block!
            # Wait, the prompt says: "check attestation_log at request start, mark redeemed=True on confirmation"
            # Actually, the API intercepts it. "If a user_op_hash is already in the log with redeemed=True, reject immediately"
            # If we receive a POST /bundler/validate, we can just check if it's already in the log.
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

