import argparse
import json
import sys
import time

import requests

from scripts.agent_fixtures import get_agent_behavior
from scripts.testnet_config import PRAMANA_API_URL, PRAMANA_ORG_ID, TEST_AGENTS


def register_agent(api_url: str, agent_addr: str, org_id: str, network: str) -> bool:
    behavior = get_agent_behavior(agent_addr)
    metadata = {
        "name": behavior.get("name", "test_agent"),
        "type": behavior.get("type", "unknown"),
        "actions": behavior.get("actions", []),
        "expected_flags": behavior.get("expected_flags", []),
        "network": network,
    }

    try:
        import jwt

        from core.config.settings import get_settings

        s = get_settings()
        token = jwt.encode({"org_id": org_id}, s.JWT_SECRET, algorithm="HS256")

        resp = requests.post(
            f"{api_url}/agents/register",
            json={
                "agent_addr": agent_addr,
                "did": f"did:pramana:{agent_addr}",
                "metadata": metadata,
            },
            headers={"X-Org-Id": org_id, "Authorization": f"Bearer {token}"},
            timeout=10,
        )
        return resp.status_code in (200, 201)
    except Exception as e:
        print(f"Registration failed for {agent_addr}: {e}")
        return False


def list_agents(api_url: str, org_id: str) -> list:
    try:
        import jwt

        from core.config.settings import get_settings

        s = get_settings()
        token = jwt.encode({"org_id": org_id}, s.JWT_SECRET, algorithm="HS256")

        headers = {"X-Org-Id": org_id, "Authorization": f"Bearer {token}"}
        for attempt in range(4):
            resp = requests.get(f"{api_url}/agents", headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("data", [])
            if resp.status_code == 429 and attempt < 3:
                time.sleep(0.35 * (attempt + 1))
                continue
            print(f"List agents returned HTTP {resp.status_code}: {resp.text[:160]}")
            return []
    except Exception as e:
        print(f"List agents failed: {e}")
    return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default=PRAMANA_API_URL)
    parser.add_argument("--org", default=PRAMANA_ORG_ID)
    parser.add_argument("--network", choices=["anvil", "base"], default="anvil")
    parser.add_argument("--count", type=int, default=10)
    args = parser.parse_args()

    agents_to_register = TEST_AGENTS[: args.count]
    print(
        f"Registering {len(agents_to_register)} agents with org_id={args.org} network={args.network}"
    )

    success_count = 0
    for i, agent_addr in enumerate(agents_to_register):
        ok = register_agent(args.api_url, agent_addr, args.org, args.network)
        status = "OK" if ok else "FAILED"
        print(f"  {i+1}/{len(agents_to_register)} {agent_addr}: {status}")
        if ok:
            success_count += 1

    print(f"\nRegistered {success_count}/{len(agents_to_register)} agents")

    registered = list_agents(args.api_url, args.org)
    print(f"Verification: {len(registered)} agents in org {args.org}")
    for agent in registered[:3]:
        print(
            f"  - {agent.get('agent_addr')} (reputation={agent.get('reputation', 0):.2f})"
        )

    return 0 if success_count == len(agents_to_register) else 1


if __name__ == "__main__":
    sys.exit(main())
