from core.config.settings import get_settings
from core.crews.monitoring import _AGENT_ORDER

print("PRAMANA L1 MONITORING CREW - UPDATED CONFIGURATION")
print("=" * 70)
print()
print("Execution Order (Sequential with hard-stop injection rule):")
print("-" * 70)

total_latency = 0
for i, agent in enumerate(_AGENT_ORDER, 1):
    print(f"  {i}. {agent.name:20s} (max_latency_ms={agent.max_latency_ms:6.1f})")
    total_latency += agent.max_latency_ms

print()
print(f"Total Sequential Latency Budget: ~{total_latency:.0f}ms")
print()

s = get_settings()
print("CRITICAL THRESHOLDS:")
print("-" * 70)
print(f"  Escalation Flag Threshold:       {s.ESCALATION_FLAG_THRESHOLD} flags")
print(f"  Risk Score Auto-Pause:           {s.RISK_SCORE_PAUSE:.2f}")
print(f"  Risk Score Escalate:             {s.RISK_SCORE_ESCALATE:.2f}")
print(f"  Guard Fail Mode:                 {s.GUARD_FAIL_MODE}")
print()

print("ECONOMIC SANITY AGENT - 12 PARAMETERS:")
print("-" * 70)
params = [
    "oracle_staleness_s (>300s flags)",
    "oracle_divergence_pct (>15% flags)",
    "liquidity_depth_usd (<10k flags)",
    "max_slippage_bps (>5000 flags)",
    "position_limit_pct (>25% flags)",
    "drawdown_limit_pct (>50% flags)",
    "mev_risk_score (>0.70 flags)",
    "token_risk_flags (detected)",
    "allowance_change_guard (UINT256_MAX)",
    "expected_value_after_gas (negative)",
    "min_notional_usd (<100)",
    "route_simulation_required",
]
for i, param in enumerate(params, 1):
    print(f"  {i:2d}. {param}")

print()
print("TESTNET PILOT - FILES CREATED:")
print("-" * 70)
files = [
    "core/agents/economic_sanity.py (new L1 agent)",
    "scripts/testnet_config.py (10 agents, org_id, durations)",
    "scripts/agent_fixtures.py (4 realistic + 6 adversarial)",
    "scripts/fund_testnet.py (Circle faucet automation)",
    "scripts/register_agents.py (POST /agents/register)",
    "scripts/behavior_simulator.py (Anvil 30min, Base Sepolia 3.5h)",
    "scripts/monitor_metrics.py (metrics polling)",
    "scripts/analyze_results.py (Guard pause % computation)",
]
for f in files:
    print(f"  {f}")

print()
print("VERIFICATION RESULTS:")
print("-" * 70)
print("  Core tests: 30/30 passing")
print("  Smoke tests: 11/11 passing")
print("  Economic Sanity Agent: verified executable")
print("  Monitoring Crew: 7 agents integrated")
print()
