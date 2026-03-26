"""
core/parsers/userop.py — Parses ERC-4337 UserOperations and adapts them to ChainEvents.
Handles both v0.6 and v0.7 structs safely, raising MalformedUserOpError on invalid types.
"""
from __future__ import annotations

import time
from typing import Any

from utils.models import ChainEvent, UserOperation
from utils.crypto import keccak256_hex


class MalformedUserOpError(Exception):
    """Raised when a UserOperation cannot be safely parsed or adapted."""
    pass


class UserOperationParser:
    
    @staticmethod
    def parse(payload: dict[str, Any]) -> UserOperation:
        """Parses a raw dict into a strictly typed UserOperation model.
        Supports both v0.6 and v0.7.
        """
        try:
            return UserOperation(**payload)
        except Exception as e:
            raise MalformedUserOpError(f"Invalid UserOperation structure: {e}")

    @staticmethod
    def adapt_to_chain_event(user_op: UserOperation, bundler_id: str = "bundler", org_id: str = "default", network: str = "anvil") -> ChainEvent:
        """Translates a parsed UserOperation into a canonical ChainEvent for L1 monitoring.
        
        The event translates as:
        - agent_id: sender
        - action: "user_operation"
        - calldata: user_op.callData
        - value_wei: 0 (UserOps themselves don't attach ETH msg.value out-of-band, it's inside the callData payload)
        - tx_hash: fake placeholder or calculated user_op_hash since tx_hash doesn't exist yet
        """
        
        # Calculate a deterministic user_op_hash from sender, nonce, and callData.
        # In actual ERC-4337 this involves hashing the packed components with chain_id and entryPoint,
        # but for internal tracing a pseudo-hash suffices before we store the real one.
        pseudo_hash_components = f"{user_op.sender}:{user_op.nonce}:{user_op.callData}:{network}"
        pseudo_op_hash = "0x" + keccak256_hex(pseudo_hash_components)

        event = ChainEvent(
            agent_id=user_op.sender,
            org_id=org_id,
            tx_hash=pseudo_op_hash,
            action="user_operation",
            calldata=user_op.callData,
            value_wei=0,
            gas_price=user_op.maxFeePerGas,
            timestamp=time.time(),
            network=network,
            user_operation=user_op
        )
        return event

