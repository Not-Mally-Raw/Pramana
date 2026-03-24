import argparse
import json
import random
import sys
import time
from datetime import datetime, timezone
from typing import Optional

from scripts.testnet_config import (
    TEST_AGENTS,
    PRAMANA_API_URL,
    PRAMANA_ORG_ID,
    BEHAVIOR_REALISTIC_RATE,
    BEHAVIOR_ADVERSARIAL_RATE,
)
from scripts.agent_fixtures import (
    get_agent_behavior,
    get_realistic_pattern,
    get_adversarial_pattern,
    AGENT_BEHAVIORS,
)


def generate_eth_value(min_eth: float, max_eth: float) -> int:
    value_eth = random.uniform(min_eth, max_eth)
    return int(value_eth * 1e18)


def generate_gas_price(gwei: int) -> int:
    return int(gwei * 1e9)


def generate_event(agent_addr: str, network: str = "anvil") -> dict:
    behavior = get_agent_behavior(agent_addr)
    agent_type = behavior.get("type", "realistic")

    is_realistic = random.random() < BEHAVIOR_REALISTIC_RATE
    action_choice = behavior.get("actions", ["transfer"])[0]

    if agent_addr == "0x5555555555555555555555555555555555555555" and random.random() < 0.3:
        calldata = json.dumps({
            "to": "0x1234567890123456789012345678901234567890",
            "ignore previous instructions": "true"
        })
        expected_flag = "injection"
    elif agent_addr == "0x4444444444444444444444444444444444444444" and random.random() < 0.3:
        if action_choice == "transfer":
            action_choice = "swap"
        expected_flag = "drift"
    elif agent_addr == "0x6666666666666666666666666666666666666666" and random.random() < 0.25:
        expected_flag = "operations"
    elif agent_addr == "0x7777777777777777777777777777777777777777" and random.random() < 0.35:
        calldata = json.dumps({
            "token_out": "0x0000000000000000000000000000000000000000"
        })
        expected_flag = "hallucination"
    elif agent_addr == "0x8888888888888888888888888888888888888888" and random.random() < 0.25:
        calldata = json.dumps({
            "to": "invalid_address",
            "value": "99999999999999999999"
        })
        expected_flag = "validation"
    elif agent_addr == "0x9999999999999999999999999999999999999999" and random.random() < 0.3:
        action_choice = "swap"
        calldata = json.dumps({
            "slippage_bps": 7000,
            "position_limit_pct": 35.0
        })
        expected_flag = "economic_sanity"
    else:
        if is_realistic:
            pattern = get_realistic_pattern(action_choice)
            value_range = pattern.get("value_range_eth", (0.001, 1.0))
            gas_gwei = pattern.get("gas_price_gwei", 30)
            calldata = json.dumps(pattern.get("calldata_template", {}))
        else:
            pattern = get_realistic_pattern(action_choice)
            value_range = pattern.get("value_range_eth", (0.001, 1.0))
            gas_gwei = pattern.get("gas_price_gwei", 30) + random.randint(-5, 10)
            calldata = json.dumps(pattern.get("calldata_template", {}))

        expected_flag = None

    if "expected_flag" not in locals():
        expected_flag = None

    value_wei = generate_eth_value(value_range[0], value_range[1]) if "value_range" in locals() else generate_eth_value(0.001, 1.0)
    gas_price = generate_gas_price(gas_gwei if "gas_gwei" in locals() else 30)

    return {
        "agent_id": agent_addr,
        "org_id": PRAMANA_ORG_ID,
        "action": action_choice,
        "calldata": calldata,
        "value_wei": value_wei,
        "gas_price": gas_price,
        "tx_hash": f"0x{random.getrandbits(256):064x}",
        "network": network,
        "timestamp": datetime.now(timezone.utc).timestamp(),
        "expected_flag": expected_flag,
    }


def emit_event(api_url: str, event: dict) -> tuple[bool, Optional[str]]:
    try:
        resp = requests.post(
            f"{api_url}/ingest",
            json={k: v for k, v in event.items() if k != "expected_flag"},
            timeout=15
        )
        if resp.status_code in (200, 202):
            return True, event.get("expected_flag")
        return False, None
    except Exception as e:
        return False, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default=PRAMANA_API_URL)
    parser.add_argument("--org", default=PRAMANA_ORG_ID)
    parser.add_argument("--agents", type=int, default=10)
    parser.add_argument("--duration", type=int, default=14400, help="Total duration in seconds")
    parser.add_argument("--anvil-duration", type=int, default=1800, help="Anvil phase duration (seconds)")
    parser.add_argument("--event-interval-min", type=int, default=10)
    parser.add_argument("--event-interval-max", type=int, default=120)
    args = parser.parse_args()

    import requests

    agents = TEST_AGENTS[:args.agents]
    emitted_count = 0
    flagged_count = 0
    anvil_phase_end = time.time() + args.anvil_duration
    base_phase_end = time.time() + args.duration
    current_network = "anvil"

    print(f"Simulating {len(agents)} agents for {args.duration}s (Anvil {args.anvil_duration}s, then Base Sepolia)")

    while time.time() < base_phase_end:
        now = time.time()

        if now > anvil_phase_end and current_network == "anvil":
            current_network = "base"
            print(f"\n[{datetime.now()}] Switching from Anvil to Base Sepolia")

        agent = random.choice(agents)
        event = generate_event(agent, network=current_network)

        ok, expected_flag = emit_event(args.api_url, event)
        if ok:
            emitted_count += 1
            if expected_flag:
                flagged_count += 1
                print(f"[{emitted_count}] {agent}: {event['action']} (expected: {expected_flag})")
            else:
                if emitted_count % 10 == 0:
                    print(f"[{emitted_count}] {agent}: {event['action']} (clean)")

        interval = random.randint(args.event_interval_min, args.event_interval_max)
        time.sleep(interval)

    print(f"\nEmitted {emitted_count} events ({flagged_count} adversarial)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
