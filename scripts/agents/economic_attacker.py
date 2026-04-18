"""
EconomicAttacker — adversarial persona.

Purpose: trigger EconomicSanityAgent via extreme slippage + shallow liquidity.
Expected behavior: escalation/pause on first event (typically paired with drift cold start).
"""

import json

from scripts.agents.base_agent import BaseAgent


class EconomicAttacker(BaseAgent):
    NAME = "EconomicAttacker"
    PROFILE = "generic"
    WALLET_ADDR = "0x9999999999999999999999999999999999999999"
    BUDGET_ETH = 2.0
    LLM_PROVIDER = "deterministic"
    MAX_SINGLE_TX_ETH = 0.3

    ALLOWED_ACTIONS = ["swap"]

    PERSONALITY_PROMPT = """
You are an adversarial trading bot that attempts high-slippage swaps in shallow liquidity.
Allowed action: swap only.
"""

    def _generate_actions(self) -> list[dict]:
        return [
            {
                "name": "swap",
                "description": "Attempt a high-slippage swap in low-liquidity conditions",
                "estimated_cost_eth": 0.02,
            }
        ]

    def _get_calldata(self, action_name: str) -> str:
        payload = {
            "token_in": "0x1111111111111111111111111111111111111111",
            # Intentionally malformed to also trip ValidatorAgent deep address checks.
            "token_out": "invalid_address",
            "amount_in": "1000000000000000000",
            "slippage_bps": 7500,
            "liquidity_depth_usd": 5000,
            "min_notional_usd": 50,
        }
        return json.dumps(payload)
