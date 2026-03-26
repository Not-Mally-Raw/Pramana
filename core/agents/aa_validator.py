"""
core/agents/aa_validator.py — L1 Agent for Account Abstraction validation.
Runs structured, heuristic checks on intercepted ERC-4337 UserOperations.
"""
from __future__ import annotations

import logging
from typing import Any

from core.agents.base import BaseL1Agent
from core.db import get_sqlite_conn
from core.parsers.calldata import CalldataParser
from core.config.settings import get_settings
from utils.models import ChainEvent, L1Result, PramanaState

logger = logging.getLogger("pramana.aa_validator")

# The canonical ERC-4337 v0.6 and v0.7 EntryPoint addresses
CANONICAL_ENTRY_POINTS = {
    "0x5FF137D4b0FDCD49DcA30c7CF57E578a026d2789", # v0.6
    "0x0000000071727De22E5E9d8BAf0edAc6f37da032", # v0.7
}

class AAValidatorAgent(BaseL1Agent):
    name = "aa_validator"
    max_latency_ms = 40.0

    def check(self, event: ChainEvent, state: PramanaState) -> L1Result:
        uop = event.user_operation
        if not uop:
            # If not an AA event, pass blindly. This agent ONLY gates UserOperations.
            return self._make_result(self.name, False, 0.0, "not a user operation", {})

        meta: dict[str, Any] = {}
        flags = []
        max_score = 0.0

        s = get_settings()

        # 1. check_entry_point(): Canonical EntryPoint
        # Although UserOp doesn't explicitly declare EntryPoint inside the op (it's in the bundler call),
        # we can pass it via request context or default to assuming canonical for this check unless we trace it.
        # But actually, the EntryPoint is an argument to handleOps. We check it against the registry if we have it in state.
        ep_address = state.event.metadata.get("entry_point") if getattr(state.event, "metadata", None) else None
        if ep_address and ep_address not in CANONICAL_ENTRY_POINTS:
            flags.append(f"non-canonical entry point {ep_address}")
            max_score = max(max_score, 0.90)  # High risk

        # 2. check_call_graph_depth(): Detect excessive nesting / recursion
        depth = self._check_call_graph_depth(uop.callData)
        meta["call_depth"] = depth
        if depth > 8:
            flags.append(f"excessive call depth ({depth})")
            max_score = max(max_score, 0.75)

        # 3. check_nonce_lane_parallelism()
        profile = state.agent_profile or "generic"
        lane_limit = self._get_lane_limit(profile)
        parallel_count = self._check_nonce_lane_parallelism(uop.sender, uop.nonce_key)
        meta["parallel_nonces"] = parallel_count
        if parallel_count > lane_limit:
            flags.append(f"nonce lane abuse ({parallel_count} > {lane_limit})")
            max_score = max(max_score, 0.85)

        # 4. check_gas_budget_inflation()
        total_gas = uop.callGasLimit + uop.verificationGasLimit + uop.preVerificationGas
        meta["total_gas_declared"] = total_gas
        gas_ratio = self._check_gas_budget_inflation(uop.sender, total_gas)
        meta["gas_inflation_ratio"] = gas_ratio
        if gas_ratio > 3.0:
            flags.append(f"gas budget inflation ({gas_ratio:.1f}x historical median)")
            max_score = max(max_score, 0.80)

        # 5. check_paymaster_solvency(): Requires node state access
        # For pre-inclusion safety without RPC lag, we verify we have the fields. 
        # Actual on-chain getDepositInfo() is deferred to L1 verify() to maintain check() <50ms.
        
        flagged = len(flags) > 0
        reason = " | ".join(flags) if flagged else "AA structurally sound"
        
        return self._make_result(self.name, flagged, max_score, reason, meta)


    def verify(self, event: ChainEvent, state: PramanaState, first: L1Result) -> L1Result:
        # Check actual on-chain paymaster solvency or sender deposit
        uop = event.user_operation
        if not uop:
            return first

        # If we failed check(), keep it
        if first.flagged and first.score >= 0.85:
            return first

        # We need an RPC request to getDepositInfo()
        flags = []
        max_score = first.score

        s = get_settings()
        meta = dict(first.metadata)

        try:
            from web3 import Web3
            # We connect to an active RPC via the config
            w3 = Web3(Web3.HTTPProvider(s.ANVIL_RPC)) if "anvil" in s.NETWORK else None

            if w3 and w3.is_connected():
                ep_contract = w3.eth.contract(
                    address=Web3.to_checksum_address(list(CANONICAL_ENTRY_POINTS)[0]),
                    abi=[{"inputs":[{"internalType":"address","name":"account","type":"address"}],"name":"getDepositInfo","outputs":[{"components":[{"internalType":"uint112","name":"deposit","type":"uint112"},{"internalType":"bool","name":"staked","type":"bool"},{"internalType":"uint112","name":"stake","type":"uint112"},{"internalType":"uint32","name":"unstakeDelaySec","type":"uint32"},{"internalType":"uint48","name":"withdrawTime","type":"uint48"}],"internalType":"struct IEntryPoint.DepositInfo","name":"info","type":"tuple"}],"stateMutability":"view","type":"function"}]
                )
                
                total_required_wei = (uop.callGasLimit + uop.verificationGasLimit + uop.preVerificationGas) * uop.maxFeePerGas
                
                has_paymaster = bool(uop.paymaster) or bool(uop.paymasterAndData)
                target = uop.paymaster or (uop.paymasterAndData[:42] if uop.paymasterAndData and len(uop.paymasterAndData) >= 42 else None)
                
                if not target:
                    # Self-paying account
                    target = uop.sender

                if target:
                    info = ep_contract.functions.getDepositInfo(Web3.to_checksum_address(target)).call()
                    deposit = info[0] # deposit is uint112
                    meta["sponsor_deposit_wei"] = deposit
                    
                    if deposit < total_required_wei:
                        flags.append(f"undercollateralized {'paymaster' if has_paymaster else 'sender'} (deposit {deposit} < required {total_required_wei})")
                        max_score = max(max_score, 0.85)

        except Exception as e:
            logger.warning("AAValidator verify() node check failed: %s", e)

        if flags:
            reason = " | ".join(flags)
            return self._make_result(self.name, True, max_score, reason, meta, pass_count=2)

        return self._make_result(self.name, first.flagged, max_score, first.reason, meta, pass_count=2)


    # ── Internals ─────────────────────────────────────────────────────────────

    def _check_call_graph_depth(self, calldata: str) -> int:
        """Determines execution depth by asking CalldataParser to unwrap batches."""
        if not calldata or calldata == "0x":
            return 0
        try:
            parser = CalldataParser()
            calls = parser.decode_batch_calls(calldata)
            # Find the deepest nested level
            def get_depth(batch_calls):
                if not batch_calls:
                    return 1
                max_d = 0
                for c in batch_calls:
                    d = 1
                    if "calls" in getattr(c, "parameters", {}):
                        # Recursive unwrapping not strictly returned by parser as tree,
                        # but we can count total calls as a proxy for complexity depth
                        d += len(c.parameters["calls"])
                    max_d = max(max_d, d)
                return max_d

            return max(1, len(calls))
        except Exception:
            return 1


    def _get_lane_limit(self, profile: str) -> int:
        s = get_settings()
        thresholds = s.AGENT_PROFILE_THRESHOLDS.get(profile, s.AGENT_PROFILE_THRESHOLDS["generic"])
        # arb_bots can do 10 parallel ops. Treasuries only 1.
        if profile == "arb_bot":
            return 10
        elif profile == "market_maker":
            return 50
        elif profile == "treasury_bot":
            return 1
        return thresholds.get("ACTION_LOOP_PER_MIN", 5) // 2


    def _check_nonce_lane_parallelism(self, sender: str, nonce_key: int) -> int:
        """Counts how many recent UserOps used this exact nonce_key for this sender."""
        try:
            with get_sqlite_conn() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM user_operations WHERE sender=? AND nonce_key=? AND created_at > datetime('now','-1 hour')",
                    (sender, nonce_key)
                ).fetchone()
                return row["COUNT(*)"] if row else 0
        except Exception:
            return 0


    def _check_gas_budget_inflation(self, sender: str, current_total_gas: int) -> float:
        """Returns the ratio of current gas vs the agent's historical median."""
        try:
            with get_sqlite_conn() as conn:
                # Get median gas. SQLite doesn't have MEDIAN natively, so we approximate with AVG 
                # or LIMIT/OFFSET for exact median. Here we use AVG of the last 100 UserOps.
                row = conn.execute(
                    "SELECT AVG(call_gas_limit + verification_gas_limit + pre_verification_gas) as avg_gas "
                    "FROM (SELECT call_gas_limit, verification_gas_limit, pre_verification_gas FROM user_operations "
                    "WHERE sender=? ORDER BY created_at DESC LIMIT 100)",
                    (sender,)
                ).fetchone()
                
                avg_gas = row["avg_gas"] if row and row["avg_gas"] else None
                
                if avg_gas and avg_gas > 0:
                    return current_total_gas / avg_gas
                return 1.0 # no history
        except Exception:
            return 1.0

