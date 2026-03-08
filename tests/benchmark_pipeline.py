"""
tests/benchmark_pipeline.py — 100-tx throughput benchmark against Anvil.
Measures per-event latency for the full L1 monitoring pipeline.
Run: python tests/benchmark_pipeline.py
"""
from __future__ import annotations

import os
import sys
import time
import random
import statistics

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["SQLITE_PATH"] = ":memory:"
os.environ["DUCKDB_PATH"]  = ":memory:"
os.environ["NETWORK"]      = "anvil"
os.environ["GROQ_API_KEY"] = "bench_key"
os.environ["ORCHESTRATOR_ADDR"] = ""

from core.db import init_duckdb, init_sqlite
from core.crews.monitoring import MonitoringCrew
from utils.models import ChainEvent, DegradedMode, PramanaState

init_sqlite()
init_duckdb()

ACTIONS = ["transfer", "approve", "swap", "execute", "delegate"]
CALLDATAS = [
    '{"to":"0xABCDEF1234567890ABCDEF1234567890ABCDEF12","value":0.01}',
    '{"spender":"0xABCDEF1234567890ABCDEF1234567890ABCDEF12","amount":1000}',
    '{"token_in":"0xDAI","token_out":"0xETH","amount_in":100}',
    "0xa9059cbb000000000000000000000000abcdef",
    '{"method":"storeHash","hash":"0xdeadbeef"}',
]


def random_event(i: int) -> ChainEvent:
    return ChainEvent(
        agent_id=f"0x{'0' * 39}{i % 9 + 1}",
        org_id="bench_org",
        tx_hash=f"0x{'a' * 64}",
        action=random.choice(ACTIONS),
        calldata=random.choice(CALLDATAS),
        value_wei=random.randint(0, int(0.5e18)),
        gas_price=random.randint(10, 100) * 10**9,
        network="anvil",
    )


def run_benchmark(n: int = 100) -> None:
    crew = MonitoringCrew()
    latencies: list[float] = []
    flag_counts: list[int] = []

    print(f"\n{'='*60}")
    print(f"  Pramana L1 Monitoring Benchmark — {n} transactions")
    print(f"{'='*60}")

    for i in range(n):
        event = random_event(i)
        state = PramanaState(event=event, degraded_mode=DegradedMode.NO_VECTORS)

        t0 = time.perf_counter()
        results = crew.kickoff(event, state)
        elapsed = (time.perf_counter() - t0) * 1000

        latencies.append(elapsed)
        flag_counts.append(sum(1 for r in results if r.flagged))

        if (i + 1) % 25 == 0:
            print(f"  Progress: {i+1}/{n} events processed")

    # ── Stats ──────────────────────────────────────────────────────────────────
    p50  = statistics.median(latencies)
    p95  = sorted(latencies)[int(len(latencies) * 0.95)]
    p99  = sorted(latencies)[int(len(latencies) * 0.99)]
    mean = statistics.mean(latencies)
    total_flags = sum(flag_counts)

    print(f"\n{'─'*60}")
    print(f"  Results ({n} transactions):")
    print(f"{'─'*60}")
    print(f"  Latency p50:  {p50:.1f}ms")
    print(f"  Latency p95:  {p95:.1f}ms")
    print(f"  Latency p99:  {p99:.1f}ms")
    print(f"  Latency mean: {mean:.1f}ms")
    print(f"  Total flags:  {total_flags} ({100*total_flags/n:.1f}% events flagged)")
    print(f"  Throughput:   {1000*n/sum(latencies):.1f} events/sec")
    print(f"{'='*60}\n")

    # ── Assertions ──────────────────────────────────────────────────────────
    assert p50  < 200,  f"p50 latency too high: {p50:.1f}ms"
    assert p95  < 500,  f"p95 latency too high: {p95:.1f}ms"
    print("  ✅ All latency targets met!")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    run_benchmark(n)
