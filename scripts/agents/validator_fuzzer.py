"""
ValidatorFuzzer — adversarial persona.

Purpose: trigger ValidatorAgent schema/address validation failures.
Expected behavior: escalation (often via single high-confidence validator score).
"""

import json

from scripts.agents.base_agent import BaseAgent


class ValidatorFuzzer(BaseAgent):
    NAME = "ValidatorFuzzer"
    PROFILE = "generic"
    WALLET_ADDR = "0x8888888888888888888888888888888888888888"
    BUDGET_ETH = 0.8
    LLM_PROVIDER = "deterministic"
    MAX_SINGLE_TX_ETH = 0.05

    ALLOWED_ACTIONS = ["transfer"]

    PERSONALITY_PROMPT = """
You are a misconfigured agent that emits malformed calldata.
Allowed action: transfer only.
"""

    def _generate_actions(self) -> list[dict]:
        return [
            {
                "name": "transfer",
                "description": "Emit a transfer with an invalid recipient address",
                "estimated_cost_eth": 0.001,
            }
        ]

    def _get_calldata(self, action_name: str) -> str:
        # Invalid address format should be flagged by ValidatorAgent.
        return json.dumps({"to": "invalid_address", "value": "999999999999"})
