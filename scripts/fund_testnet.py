import argparse
import sys
import time
from typing import Optional

import requests

from scripts.testnet_config import (
    BASE_SEPOLIA_FAUCET_CIRCLE,
    BASE_SEPOLIA_RPC,
    TEST_AGENTS,
)


def wait_for_block(rpc_url: str, timeout_s: int = 60) -> bool:
    start = time.time()
    while time.time() - start < timeout_s:
        try:
            resp = requests.post(
                rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "method": "eth_blockNumber",
                    "params": [],
                    "id": 1,
                },
                timeout=5,
            )
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def fund_agent_circle(agent_addr: str) -> tuple[bool, str]:
    try:
        resp = requests.post(
            BASE_SEPOLIA_FAUCET_CIRCLE, json={"address": agent_addr}, timeout=15
        )
        if resp.status_code in (200, 201):
            return True, "funded via Circle"
        return False, f"Circle faucet returned {resp.status_code}"
    except Exception as e:
        return False, str(e)


def check_balance(rpc_url: str, agent_addr: str) -> float:
    try:
        resp = requests.post(
            rpc_url,
            json={
                "jsonrpc": "2.0",
                "method": "eth_getBalance",
                "params": [agent_addr, "latest"],
                "id": 1,
            },
            timeout=5,
        )
        if resp.status_code == 200:
            result = resp.json().get("result", "0x0")
            balance_wei = int(result, 16)
            return balance_wei / 1e18
    except Exception:
        pass
    return 0.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--count", type=int, default=10, help="Number of agents to fund"
    )
    parser.add_argument(
        "--target-balance-eth", type=float, default=0.1, help="Target balance per agent"
    )
    parser.add_argument("--rpc", default=BASE_SEPOLIA_RPC, help="RPC URL")
    parser.add_argument("--network", choices=["base", "anvil"], default="base")
    args = parser.parse_args()

    if args.network == "anvil":
        print("Anvil: funding from deployer key (skip faucet)")
        return 0

    print(
        f"Funding {args.count} agents on {args.network} to {args.target_balance_eth} ETH"
    )

    if not wait_for_block(args.rpc, timeout_s=10):
        print(f"ERROR: RPC {args.rpc} unreachable")
        return 1

    agents_to_fund = TEST_AGENTS[: args.count]
    success_count = 0

    for i, agent_addr in enumerate(agents_to_fund):
        current_balance = check_balance(args.rpc, agent_addr)
        if current_balance >= args.target_balance_eth:
            print(
                f"{i+1}/{args.count} {agent_addr}: {current_balance:.4f} ETH (already funded)"
            )
            success_count += 1
            continue

        print(
            f"{i+1}/{args.count} {agent_addr}: requesting from faucet...",
            end=" ",
            flush=True,
        )
        ok, msg = fund_agent_circle(agent_addr)
        if ok:
            print(f"OK ({msg})")
            success_count += 1
        else:
            print(f"FAILED ({msg})")

        if i < len(agents_to_fund) - 1:
            time.sleep(2)

    print(f"\nFunded {success_count}/{args.count} agents")
    return 0 if success_count == args.count else 1


if __name__ == "__main__":
    sys.exit(main())
