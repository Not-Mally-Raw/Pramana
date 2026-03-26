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
        if "paymasterPostOpGasLimit" in payload or "paymasterVerificationGasLimit" in payload:
            payload["version"] = "v0.7"
        else:
            payload["version"] = "v0.6"
            
        try:
            return UserOperation(**payload)
        except Exception as e:
            raise MalformedUserOpError(f"Invalid UserOperation structure: {e}")

    @staticmethod
    def get_canonical_user_op_hash(user_op: UserOperation, entry_point: str, chain_id: int) -> str:
        from web3 import Web3
        from eth_abi import encode
        
        # 1. Pack the UserOperation based on version
        if user_op.version == "v0.6":
            init_code = user_op.initCode or "0x"
            pnd = user_op.paymasterAndData or "0x"
            
            packed_hash = Web3.keccak(encode(
                ["address", "uint256", "bytes32", "bytes32", "uint256", "uint256", "uint256", "uint256", "uint256", "bytes32"],
                [
                    Web3.to_checksum_address(user_op.sender),
                    user_op.nonce,
                    Web3.keccak(hexstr=init_code),
                    Web3.keccak(hexstr=user_op.callData),
                    user_op.callGasLimit,
                    user_op.verificationGasLimit,
                    user_op.preVerificationGas,
                    user_op.maxFeePerGas,
                    user_op.maxPriorityFeePerGas,
                    Web3.keccak(hexstr=pnd)
                ]
            ))
        else:
            # v0.7
            if user_op.initCode:
                init_code = user_op.initCode
            elif user_op.factory:
                init_code = user_op.factory + (user_op.factoryData or "")[2:]
            else:
                init_code = "0x"
                
            if user_op.paymasterAndData:
                pnd = user_op.paymasterAndData
            elif user_op.paymaster:
                pd = user_op.paymasterData or "0x"
                vgas = hex(user_op.paymasterVerificationGasLimit or 0)[2:].zfill(32)
                pgas = hex(user_op.paymasterPostOpGasLimit or 0)[2:].zfill(32)
                pnd = user_op.paymaster + vgas + pgas + pd[2:]
            else:
                pnd = "0x"
                
            account_gas_limits = (user_op.verificationGasLimit << 128) | user_op.callGasLimit
            gas_fees = (user_op.maxPriorityFeePerGas << 128) | user_op.maxFeePerGas
            
            packed_hash = Web3.keccak(encode(
                ["address", "uint256", "bytes32", "bytes32", "bytes32", "uint256", "bytes32", "bytes32"],
                [
                    Web3.to_checksum_address(user_op.sender),
                    user_op.nonce,
                    Web3.keccak(hexstr=init_code),
                    Web3.keccak(hexstr=user_op.callData),
                    Web3.keccak(account_gas_limits.to_bytes(32, 'big')),
                    user_op.preVerificationGas,
                    Web3.keccak(gas_fees.to_bytes(32, 'big')),
                    Web3.keccak(hexstr=pnd)
                ]
            ))
            
        # 2. Final Hash
        final_hash = Web3.keccak(encode(
            ["bytes32", "address", "uint256"],
            [packed_hash, Web3.to_checksum_address(entry_point), chain_id]
        ))
        return Web3.to_hex(final_hash)


    @staticmethod
    def adapt_to_chain_event(user_op: UserOperation, entry_point: str, chain_id: int, bundler_id: str = "bundler", org_id: str = "default", network: str = "anvil") -> ChainEvent:
        """Translates a parsed UserOperation into a canonical ChainEvent for L1 monitoring."""
        
        canonical_op_hash = UserOperationParser.get_canonical_user_op_hash(user_op, entry_point, chain_id)

        event = ChainEvent(
            agent_id=user_op.sender,
            org_id=org_id,
            tx_hash=canonical_op_hash,
            action="userop",
            calldata=user_op.callData,
            value_wei=0,
            gas_price=user_op.maxFeePerGas,
            timestamp=time.time(),
            network=network,
            bundler_id=bundler_id,
            user_operation=user_op
        )
        return event

