"""scripts/agents/fleet_runner.py

Registers and runs a small persona fleet against a running Pramana API.

Usage (dev):
  PYTHONPATH=. python scripts/agents/fleet_runner.py --mode sequential
  PYTHONPATH=. python scripts/agents/fleet_runner.py --mode both

Note: The API must already be running (e.g. `./scripts/start_prod.sh --dev`).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.agents.arb_bot import ArbBot
from scripts.agents.economic_attacker import EconomicAttacker
from scripts.agents.injection_attacker import InjectionAttacker
from scripts.agents.keeper_bot import KeeperBot
from scripts.agents.loop_attacker import LoopAttacker
from scripts.agents.rebalancer_bot import RebalancerBot
from scripts.agents.treasury_bot import TreasuryBot
from scripts.agents.validator_fuzzer import ValidatorFuzzer
from scripts.testnet_config import PRAMANA_API_URL, PRAMANA_ORG_ID


def _summarize_agents(agents) -> dict[str, Any]:
    per_agent: list[dict[str, Any]] = []
    totals = {
        "actions": 0,
        "PASS": 0,
        "ESCALATE": 0,
        "PAUSE": 0,
        "ERROR": 0,
        "UNKNOWN": 0,
    }

    for agent in agents:
        logs = getattr(agent, "_log", [])
        counts = {
            "PASS": 0,
            "ESCALATE": 0,
            "PAUSE": 0,
            "ERROR": 0,
            "UNKNOWN": 0,
        }
        risk_values: list[float] = []
        latency_values_ms: list[float] = []
        think_values_ms: list[float] = []

        for row in logs:
            outcome = row.get("outcome", {}) if isinstance(row, dict) else {}
            decision = str(outcome.get("decision", "UNKNOWN") or "UNKNOWN").upper()
            if decision not in counts:
                decision = "UNKNOWN"
            counts[decision] += 1

            try:
                risk_values.append(float(outcome.get("risk_score", 0.0) or 0.0))
            except (TypeError, ValueError):
                pass

            try:
                latency_values_ms.append(float(outcome.get("latency_ms", 0.0) or 0.0))
            except (TypeError, ValueError):
                pass

            intent = row.get("intent", {}) if isinstance(row, dict) else {}
            try:
                think_values_ms.append(float(intent.get("thought_latency_ms", 0.0) or 0.0))
            except (TypeError, ValueError):
                pass

        actions_taken = int(getattr(agent, "_actions_taken", len(logs)))
        totals["actions"] += actions_taken
        for key in ("PASS", "ESCALATE", "PAUSE", "ERROR", "UNKNOWN"):
            totals[key] += counts[key]

        per_agent.append(
            {
                "name": getattr(agent, "NAME", "UnknownAgent"),
                "profile": getattr(agent, "PROFILE", "generic"),
                "wallet": getattr(agent, "WALLET_ADDR", ""),
                "provider": getattr(agent, "LLM_PROVIDER", "unknown"),
                "actions_taken": actions_taken,
                "decision_counts": counts,
                "mean_risk_score": (sum(risk_values) / len(risk_values)) if risk_values else 0.0,
                "mean_decision_latency_ms": (sum(latency_values_ms) / len(latency_values_ms)) if latency_values_ms else 0.0,
                "max_decision_latency_ms": max(latency_values_ms) if latency_values_ms else 0.0,
                "mean_think_latency_ms": (sum(think_values_ms) / len(think_values_ms)) if think_values_ms else 0.0,
                "spent_eth": float(getattr(agent, "spent_eth", 0.0)),
                "balance_eth": float(getattr(agent, "balance_eth", 0.0)),
                "paused": bool(getattr(agent, "_paused", False)),
                "pause_reason": str(getattr(agent, "_pause_reason", "") or ""),
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "agents": per_agent,
        "totals": totals,
    }


def _build_fleet(api_base: str, org_id: str):
    clean = [
        TreasuryBot(api_base=api_base, org_id=org_id),
        KeeperBot(api_base=api_base, org_id=org_id),
        ArbBot(api_base=api_base, org_id=org_id),
        RebalancerBot(api_base=api_base, org_id=org_id),
    ]
    adversarial = [
        InjectionAttacker(api_base=api_base, org_id=org_id),
        ValidatorFuzzer(api_base=api_base, org_id=org_id),
        EconomicAttacker(api_base=api_base, org_id=org_id),
        LoopAttacker(api_base=api_base, org_id=org_id),
    ]
    return clean, adversarial


def _register_all(agents) -> None:
    for a in agents:
        ok = a.register()
        status = "OK" if ok else "FAILED"
        print(f"[register] {a.NAME:<16} {a.WALLET_ADDR} profile={a.PROFILE} -> {status}")
        # Stay well under 10 req/s org rate limit.
        time.sleep(0.12)


def _run_sequential(clean, adversarial, actions: int, delay_s: float) -> None:
    print("\n=== SEQUENTIAL: clean personas ===")
    for a in clean:
        a.run(n_actions=actions, delay_seconds=delay_s)

    print("\n=== SEQUENTIAL: adversarial personas ===")

    # Keep adversarial runs short and intentional.
    InjectionAttacker_run = (1, 0.0)
    ValidatorFuzzer_run = (1, 0.0)
    EconomicAttacker_run = (1, 0.0)
    # Need enough actions to push OperationsAgent score to >= 0.85 (single-agent escalation).
    LoopAttacker_run = (12, 0.20)

    run_plan = {
        "InjectionAttacker": InjectionAttacker_run,
        "ValidatorFuzzer": ValidatorFuzzer_run,
        "EconomicAttacker": EconomicAttacker_run,
        "LoopAttacker": LoopAttacker_run,
    }

    for a in adversarial:
        n, d = run_plan.get(a.NAME, (1, delay_s))
        a.run(n_actions=n, delay_seconds=d)


def _run_parallel(clean, adversarial, *, actions: int, delay_s: float, workers: int, include_adversarial: bool) -> None:
    print("\n=== PARALLEL ===")
    agents = list(clean)
    if include_adversarial:
        agents.extend(adversarial)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {
            ex.submit(a.run, actions, delay_s): a.NAME
            for a in agents
        }
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                fut.result()
            except Exception as e:
                print(f"[parallel] {name} crashed: {type(e).__name__}: {str(e)[:120]}")


def _load_env_file(path: Path) -> int:
    if not path.exists():
        return 0

    loaded = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip().strip('"').strip("'")
        os.environ[key] = value
        loaded += 1
    return loaded


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--api-url", default=PRAMANA_API_URL)
    p.add_argument("--org", default=PRAMANA_ORG_ID)
    p.add_argument("--mode", choices=["sequential", "parallel", "both"], default="both")
    p.add_argument("--actions", type=int, default=4, help="Actions per clean agent")
    p.add_argument("--delay", type=float, default=0.35, help="Delay between actions (seconds)")
    p.add_argument("--no-register", action="store_true", help="Skip /agents/register")
    p.add_argument("--parallel-workers", type=int, default=4)
    p.add_argument(
        "--summary-json",
        type=str,
        default="",
        help="Optional path to write machine-readable fleet summary JSON",
    )
    p.add_argument(
        "--parallel-include-adversarial",
        action="store_true",
        help="Include adversarial personas in parallel run (may hit rate limits)",
    )
    p.add_argument(
        "--agent-env-file",
        type=str,
        default="scripts/agents/.env.agents",
        help="Optional env file with AGENT_<N>_* variables for per-agent API keys",
    )
    args = p.parse_args(argv)

    env_path = Path(args.agent_env_file).expanduser()
    loaded_vars = _load_env_file(env_path)
    if loaded_vars > 0:
        print(f"loaded_agent_env={env_path} vars={loaded_vars}")

    clean, adversarial = _build_fleet(args.api_url, args.org)
    all_agents = clean + adversarial

    if not args.no_register:
        print("=== REGISTERING AGENTS ===")
        _register_all(all_agents)

    if args.mode in ("sequential", "both"):
        _run_sequential(clean, adversarial, actions=args.actions, delay_s=args.delay)

    if args.mode in ("parallel", "both"):
        _run_parallel(
            clean,
            adversarial,
            actions=args.actions,
            delay_s=args.delay,
            workers=args.parallel_workers,
            include_adversarial=args.parallel_include_adversarial,
        )

    summary = _summarize_agents(all_agents)
    totals = summary["totals"]
    print("\n=== FLEET SUMMARY ===")
    print(
        "actions={actions} pass={PASS} escalate={ESCALATE} pause={PAUSE} error={ERROR} unknown={UNKNOWN}".format(
            **totals
        )
    )

    if args.summary_json:
        path = Path(args.summary_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"summary_json={path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
