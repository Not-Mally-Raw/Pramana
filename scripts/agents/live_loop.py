from __future__ import annotations

import os
import time

from scripts.agents.arb_bot import ArbBot
from scripts.agents.injection_attacker import InjectionAttacker
from scripts.agents.keeper_bot import KeeperBot
from scripts.agents.rebalancer_bot import RebalancerBot
from scripts.agents.treasury_bot import TreasuryBot


def main() -> int:
    api_base = os.environ.get("PRAMANA_API_URL", "http://127.0.0.1:8000")
    org_id = os.environ.get("PRAMANA_ORG_ID", "demo-prof")

    agents = [
        TreasuryBot(api_base=api_base, org_id=org_id),
        KeeperBot(api_base=api_base, org_id=org_id),
        ArbBot(api_base=api_base, org_id=org_id),
        RebalancerBot(api_base=api_base, org_id=org_id),
        InjectionAttacker(api_base=api_base, org_id=org_id),
    ]

    for agent in agents:
        agent.register()

    cycle = 0
    while True:
        cycle += 1
        lines: list[str] = []
        for agent in agents:
            try:
                intent = agent._think(agent._generate_actions())
                outcome = agent._submit_to_pramana(intent)
                lines.append(f"{agent.NAME}:{outcome.decision}:{outcome.risk_score:.3f}")
            except Exception as e:
                lines.append(f"{agent.NAME}:ERROR:{str(e)[:48]}")

        print(f"[live-cycle {cycle}] " + " | ".join(lines), flush=True)
        time.sleep(4)


if __name__ == "__main__":
    raise SystemExit(main())
