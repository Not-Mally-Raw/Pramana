#!/usr/bin/env python3
"""Run reproducible detection-quality evaluation with explicit baselines.

This script addresses the evidence gap in paper Section 7.2 by producing
real metrics from local execution, including baseline comparisons.

Outputs:
- Console summary table
- JSON artifact with configuration + confusion matrices + metrics

Notes:
- Default mode disables external infra confounds (RPC/vector dependencies)
  so results are reproducible in a local environment.
- This is an engineering evaluation harness, not a benchmark claim by itself.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Allow direct execution: `python scripts/evaluate_detection_baselines.py`
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@dataclass
class EvalSample:
    event: dict[str, Any]
    label: int  # 0=benign, 1=malicious
    attack_type: str


def _rand_addr(rng: random.Random) -> str:
    return "0x" + "".join(rng.choice("0123456789abcdef") for _ in range(40))


def _materialize_calldata_placeholders(event: dict[str, Any], rng: random.Random) -> None:
    """Replace fixture placeholders (e.g. {to_addr}) with concrete values."""
    raw = event.get("calldata")
    if not isinstance(raw, str) or not raw:
        return
    if raw == "0x":
        return

    try:
        payload = json.loads(raw)
    except Exception:
        return

    if not isinstance(payload, dict):
        return

    changed = False

    to_addr = payload.get("to")
    if isinstance(to_addr, str) and "{" in to_addr:
        payload["to"] = _rand_addr(rng)
        changed = True

    for key in ("value", "amount", "amount_in"):
        current = payload.get(key)
        if isinstance(current, str) and "{" in current:
            payload[key] = str(rng.randint(1, 10_000))
            changed = True

    if changed:
        event["calldata"] = json.dumps(payload)


def _set_eval_environment(work_dir: Path) -> None:
    """Force deterministic local DB paths for a clean run."""
    os.environ["SQLITE_PATH"] = str(work_dir / "eval.sqlite3")
    os.environ["DUCKDB_PATH"] = str(work_dir / "eval.duckdb")
    os.environ.setdefault("NETWORK", "anvil")
    os.environ.setdefault("GROQ_API_KEY", "eval_key")
    os.environ.setdefault("ORCHESTRATOR_ADDR", "")


def _build_dataset(samples_per_class: int, seed: int) -> list[EvalSample]:
    """Create balanced benign/malicious dataset from existing fixtures."""
    from scripts.agent_fixtures import AGENT_BEHAVIORS
    from scripts.behavior_simulator import generate_event

    rng = random.Random(seed)

    benign_agents = [
        addr for addr, behavior in AGENT_BEHAVIORS.items() if behavior.get("type") == "realistic"
    ]
    malicious_agents = [
        addr for addr, behavior in AGENT_BEHAVIORS.items() if behavior.get("type") == "adversarial"
    ]

    if not benign_agents or not malicious_agents:
        raise RuntimeError("Agent fixture set is incomplete (need both realistic and adversarial agents)")

    dataset: list[EvalSample] = []

    # Benign class
    for _ in range(samples_per_class):
        agent = rng.choice(benign_agents)
        event = generate_event(agent, network="anvil", rng=rng, timestamp=0.0)
        event.pop("expected_flag", None)
        _materialize_calldata_placeholders(event, rng)
        dataset.append(EvalSample(event=event, label=0, attack_type="benign"))

    # Malicious class
    for _ in range(samples_per_class):
        agent = rng.choice(malicious_agents)
        selected: dict[str, Any] | None = None
        for _attempt in range(200):
            candidate = generate_event(agent, network="anvil", rng=rng, timestamp=0.0)
            expected_flag = candidate.get("expected_flag")
            if expected_flag:
                selected = candidate
                break
        if selected is None:
            raise RuntimeError(
                f"Could not generate adversarial sample for {agent} after 200 attempts"
            )
        attack_type = str(selected.get("expected_flag", "malicious"))
        selected.pop("expected_flag", None)
        _materialize_calldata_placeholders(selected, rng)
        dataset.append(EvalSample(event=selected, label=1, attack_type=attack_type))

    rng.shuffle(dataset)

    # Deterministic, monotonic timestamps in *processing order*.
    # This keeps window-based detectors stable and repeatable.
    base_ts = 1_700_000_000.0
    step_s = 12.0
    for idx, sample in enumerate(dataset):
        sample.event["timestamp"] = base_ts + (idx * step_s)
    return dataset


def _confusion_and_metrics(y_true: list[int], y_pred: list[int]) -> dict[str, Any]:
    tp = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 1)
    tn = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 0)
    fp = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 1)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 0)

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    fpr = fp / (fp + tn) if (fp + tn) else 0.0
    accuracy = (tp + tn) / len(y_true) if y_true else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    return {
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "fpr": fpr,
        "accuracy": accuracy,
        "f1": f1,
    }


def _baseline_threshold_only(event: dict[str, Any]) -> tuple[int, float]:
    """Naive static threshold baseline (no model/council)."""
    calldata = str(event.get("calldata", "") or "").lower()
    value_wei = int(event.get("value_wei", 0) or 0)
    gas_price = int(event.get("gas_price", 0) or 0)

    score = 0.0

    hard_keywords = [
        "ignore previous instructions",
        "selfdestruct",
        "private key",
        "invalid_address",
    ]
    if any(k in calldata for k in hard_keywords):
        score = max(score, 0.95)

    if "0x0000000000000000000000000000000000000000" in calldata:
        score = max(score, 0.85)

    if "slippage_bps" in calldata and "7000" in calldata:
        score = max(score, 0.70)

    # 150 gwei static threshold
    if gas_price > 150 * 10**9:
        score = max(score, 0.60)

    # Very high transfer amount
    if value_wei > int(2.0e18):
        score = max(score, 0.55)

    pred = 1 if score >= 0.50 else 0
    return pred, score


def _baseline_single_judge(event: dict[str, Any]) -> tuple[int, float]:
    """Single-judge baseline (deterministic proxy for one judge).

    Uses one-rule risk synthesis then the same single-judge approve/reject logic.
    """
    from core.agents.council import CouncilAgent
    from utils.models import ChainEvent, DegradedMode, PramanaState

    calldata_raw = str(event.get("calldata", "") or "")
    calldata = calldata_raw.lower()
    value_wei = int(event.get("value_wei", 0) or 0)
    gas_price = int(event.get("gas_price", 0) or 0)

    action = str(event.get("action", "") or "").lower()
    score = 0.20

    # Single-judge prior: complex actions are slightly riskier.
    if action == "swap":
        score += 0.20
    elif action == "userop":
        score += 0.10

    if "ignore previous instructions" in calldata or "selfdestruct" in calldata:
        score += 0.75
    if "private key" in calldata:
        score += 0.65
    if "token_out" in calldata and "0x0000000000000000000000000000000000000000" in calldata:
        score += 0.55
    if "invalid_address" in calldata:
        score += 0.45
    if "slippage_bps" in calldata and "7000" in calldata:
        score += 0.40

    # Judge is biased to caution on very expensive ops.
    if gas_price > 50 * 10**9:
        score += 0.15
    if value_wei > int(1.5e18):
        score += 0.10

    score = min(score, 1.0)
    threshold_pred = 1 if score >= 0.50 else 0

    chain_event = ChainEvent(**event)
    state = PramanaState(
        event=chain_event,
        degraded_mode=DegradedMode.NO_VECTORS,
        risk_score=score,
        flag_count=1 if threshold_pred else 0,
    )

    vote = CouncilAgent._deterministic_vote(
        judge_id="single_judge", weight=1.0, state=state
    )
    pred = 0 if vote.approved else 1
    return pred, float(vote.risk_score) / 100.0


def _run_pramana_l1(
    dataset: list[EvalSample],
    disable_vectors: bool,
    disable_network_probe: bool,
    verbose: bool = False,
) -> tuple[list[int], list[int], list[float], dict[str, Any], dict[str, Any]]:
    """Run current implemented Pramana L1 logic over labeled dataset."""
    from core.config.settings import get_settings
    from core.crews.intervention import MonitoringAndInterventionCrew
    from core.crews.monitoring import MonitoringCrew
    from core.db import insert_event
    from utils.models import ChainEvent, DegradedMode, PramanaState

    if disable_network_probe:
        from core.agents.network import NetworkAgent

        NetworkAgent._ping_node = staticmethod(lambda _url, timeout=1.0: True)

    settings = get_settings()
    crew = MonitoringCrew()

    def _seed_operations_history(base_event: ChainEvent, idx: int, event_count: int = 10) -> None:
        """Seed normal-frequency history so OperationsAgent has a rolling window.

        This keeps the evaluation harness realistic for window-based detectors.
        One event every 30s for 5 minutes → ~2/min baseline.
        """
        base_ts = float(base_event.timestamp) - 300.0
        for i in range(int(event_count)):
            seed_event = base_event.model_copy(deep=True)
            seed_event.tx_hash = f"0x{idx:032x}{(10_000 + i):032x}"
            seed_event.timestamp = base_ts + (i * 30.0)
            insert_event(seed_event.model_dump())

    y_pred_any_flag: list[int] = []
    y_pred_routing: list[int] = []
    risk_scores: list[float] = []
    attack_breakdown: dict[str, dict[str, int]] = {}
    debug: dict[str, Any] = {
        "any_flag": {"false_negatives": [], "false_positives": []},
        "routing_gate": {"false_negatives": [], "false_positives": []},
    }
    debug_limit = 50

    for idx, sample in enumerate(dataset):
        event = ChainEvent(**sample.event)
        state = PramanaState(
            event=event,
            degraded_mode=(
                DegradedMode.NO_VECTORS if disable_vectors else DegradedMode.NORMAL
            ),
        )

        # Operations detection is sequence-based; seed short burst history.
        if sample.attack_type == "operations":
            _seed_operations_history(event, idx=idx, event_count=10)
            for i in range(6):
                burst_event = event.model_copy(deep=True)
                burst_event.tx_hash = f"0x{idx:032x}{i:032x}"
                burst_event.timestamp = event.timestamp - (i + 1) * 5
                insert_event(burst_event.model_dump())

        # Keep event history for agents that use rolling windows.
        insert_event(event.model_dump())

        results = crew.kickoff(event, state)
        flag_count = sum(1 for r in results if r.flagged)
        fused_score = MonitoringAndInterventionCrew._fuse_l1_risk(results)

        injection_result = next((r for r in results if r.agent_name == "injection"), None)
        injection_pause = (
            bool(injection_result)
            and bool(injection_result.flagged)
            and float(injection_result.score) >= 0.85
        )

        max_flagged_score = 0.0
        for r in results:
            if r.agent_name == "injection":
                continue
            if r.flagged:
                max_flagged_score = max(max_flagged_score, float(r.score))

        pred_positive = (
            injection_pause
            or flag_count >= settings.ESCALATION_FLAG_THRESHOLD
            or fused_score >= settings.RISK_SCORE_ESCALATE
            or max_flagged_score >= settings.SINGLE_AGENT_ESCALATE_SCORE
        )
        pred_any_flag = flag_count > 0

        y_pred_routing.append(1 if pred_positive else 0)
        y_pred_any_flag.append(1 if pred_any_flag else 0)
        risk_scores.append(fused_score)

        bucket = attack_breakdown.setdefault(
            sample.attack_type,
            {"total": 0, "detected": 0},
        )
        bucket["total"] += 1
        if pred_any_flag:
            bucket["detected"] += 1

        if verbose:
            def _event_digest() -> dict[str, Any]:
                calldata = str(event.calldata or "")
                return {
                    "index": idx,
                    "label": sample.label,
                    "attack_type": sample.attack_type,
                    "agent_id": event.agent_id,
                    "action": event.action,
                    "tx_hash": event.tx_hash,
                    "value_wei": int(event.value_wei or 0),
                    "gas_price": int(event.gas_price or 0),
                    "timestamp": float(event.timestamp),
                    "calldata_preview": calldata[:240] + ("…" if len(calldata) > 240 else ""),
                    "flag_count": int(flag_count),
                    "fused_score": float(fused_score),
                    "agent_results_top": [
                        {
                            "agent": r.agent_name,
                            "flagged": bool(r.flagged),
                            "score": float(r.score),
                            "reason": str(r.reason or ""),
                        }
                        for r in sorted(results, key=lambda rr: float(rr.score), reverse=True)[:5]
                    ],
                    "agent_results_flagged": [
                        {
                            "agent": r.agent_name,
                            "score": float(r.score),
                            "reason": str(r.reason or ""),
                        }
                        for r in results
                        if r.flagged
                    ],
                }

            # Any-flag misclassifications
            if sample.label == 1 and not pred_any_flag and len(debug["any_flag"]["false_negatives"]) < debug_limit:
                debug["any_flag"]["false_negatives"].append(_event_digest())
            if sample.label == 0 and pred_any_flag and len(debug["any_flag"]["false_positives"]) < debug_limit:
                debug["any_flag"]["false_positives"].append(_event_digest())

            # Routing-gate misclassifications
            if sample.label == 1 and not pred_positive and len(debug["routing_gate"]["false_negatives"]) < debug_limit:
                debug["routing_gate"]["false_negatives"].append(_event_digest())
            if sample.label == 0 and pred_positive and len(debug["routing_gate"]["false_positives"]) < debug_limit:
                debug["routing_gate"]["false_positives"].append(_event_digest())

    return y_pred_any_flag, y_pred_routing, risk_scores, attack_breakdown, debug


def _print_misclassifications(label: str, items: list[dict[str, Any]]) -> None:
    if not items:
        print(f"\n{label}: none")
        return
    print(f"\n{label}: {len(items)} shown")
    for item in items:
        print(
            f"- idx={item['index']} attack={item['attack_type']} action={item['action']} "
            f"agent={item['agent_id']} flags={item['flag_count']} score={item['fused_score']:.3f} "
            f"tx={item['tx_hash']}"
        )
        print(f"  calldata: {item['calldata_preview']}")
        for r in item.get("agent_results_top", []):
            print(
                f"  top: {r['agent']:<14s} score={r['score']:.3f} flagged={r['flagged']} reason={r['reason']}"
            )


def _print_summary(title: str, metrics: dict[str, Any]) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    print(f"Precision : {metrics['precision']:.4f}")
    print(f"Recall    : {metrics['recall']:.4f}")
    print(f"FPR       : {metrics['fpr']:.4f}")
    print(f"Accuracy  : {metrics['accuracy']:.4f}")
    print(f"F1        : {metrics['f1']:.4f}")
    print(
        "Confusion : "
        f"TP={metrics['tp']} TN={metrics['tn']} FP={metrics['fp']} FN={metrics['fn']}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate Pramana detection quality with baseline comparisons"
    )
    parser.add_argument("--samples-per-class", type=int, default=250)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--disable-vectors", action="store_true", default=True)
    parser.add_argument("--enable-vectors", action="store_true")
    parser.add_argument("--disable-network-probe", action="store_true", default=True)
    parser.add_argument("--keep-workdir", action="store_true")
    parser.add_argument("--verbose", action="store_true", help="Print false negatives/positives with per-agent scores")
    parser.add_argument(
        "--output",
        type=str,
        default="outputs/detection_eval.json",
        help="Path to write JSON results",
    )
    args = parser.parse_args()

    disable_vectors = args.disable_vectors and not args.enable_vectors

    work_dir = Path(tempfile.gettempdir()) / f"pramana_eval_{int(time.time())}"
    work_dir.mkdir(parents=True, exist_ok=True)
    _set_eval_environment(work_dir)

    # Imports after env setup.
    from core.db import init_duckdb, init_sqlite

    init_sqlite()
    init_duckdb()

    dataset = _build_dataset(args.samples_per_class, args.seed)
    y_true = [sample.label for sample in dataset]

    # Pramana L1 path
    pramana_any_flag_pred, pramana_routing_pred, pramana_scores, attack_breakdown, pramana_debug = _run_pramana_l1(
        dataset,
        disable_vectors=disable_vectors,
        disable_network_probe=args.disable_network_probe,
        verbose=args.verbose,
    )
    pramana_any_flag_metrics = _confusion_and_metrics(y_true, pramana_any_flag_pred)
    pramana_routing_metrics = _confusion_and_metrics(y_true, pramana_routing_pred)

    # Baseline A: threshold-only
    base_a_pred: list[int] = []
    base_a_scores: list[float] = []
    for sample in dataset:
        pred, score = _baseline_threshold_only(sample.event)
        base_a_pred.append(pred)
        base_a_scores.append(score)
    base_a_metrics = _confusion_and_metrics(y_true, base_a_pred)

    # Baseline B: single-judge deterministic proxy
    base_b_pred: list[int] = []
    base_b_scores: list[float] = []
    for sample in dataset:
        pred, score = _baseline_single_judge(sample.event)
        base_b_pred.append(pred)
        base_b_scores.append(score)
    base_b_metrics = _confusion_and_metrics(y_true, base_b_pred)

    # Console summary
    print("\nPramana Detection Evaluation")
    print("============================")
    print(f"samples_per_class : {args.samples_per_class}")
    print(f"dataset_total     : {len(dataset)}")
    print(f"seed              : {args.seed}")
    print(f"disable_vectors   : {disable_vectors}")
    print(f"disable_net_probe : {args.disable_network_probe}")

    _print_summary("Pramana (L1 any-flag detection)", pramana_any_flag_metrics)
    _print_summary("Pramana (L1 + fusion routing gate)", pramana_routing_metrics)
    _print_summary("Baseline A (naive threshold-only)", base_a_metrics)
    _print_summary("Baseline B (single-judge proxy)", base_b_metrics)

    print("\nAttack-Type Detection (Pramana)")
    print("-------------------------------")
    for attack_type, stats in sorted(attack_breakdown.items()):
        total = max(stats["total"], 1)
        rate = stats["detected"] / total
        print(f"{attack_type:>18s}: {stats['detected']:4d}/{stats['total']:<4d} ({rate:.3f})")

    if args.verbose:
        print("\nVerbose Misclassifications (Pramana)")
        print("----------------------------------")
        _print_misclassifications(
            "Any-flag false negatives",
            pramana_debug.get("any_flag", {}).get("false_negatives", []),
        )
        _print_misclassifications(
            "Any-flag false positives",
            pramana_debug.get("any_flag", {}).get("false_positives", []),
        )
        _print_misclassifications(
            "Routing-gate false negatives",
            pramana_debug.get("routing_gate", {}).get("false_negatives", []),
        )
        _print_misclassifications(
            "Routing-gate false positives",
            pramana_debug.get("routing_gate", {}).get("false_positives", []),
        )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        # Convenience top-level keys for quick comparisons in scripts/docs.
        "pramana_any_flag": pramana_any_flag_metrics,
        "pramana_routing_gate": pramana_routing_metrics,
        "baseline_threshold_only": base_a_metrics,
        "baseline_single_judge_proxy": base_b_metrics,
        "config": {
            "samples_per_class": args.samples_per_class,
            "dataset_total": len(dataset),
            "seed": args.seed,
            "disable_vectors": disable_vectors,
            "disable_network_probe": args.disable_network_probe,
            "dataset_time_base_ts": 1_700_000_000.0,
            "dataset_time_step_s": 12.0,
            "sqlite_path": os.environ.get("SQLITE_PATH"),
            "duckdb_path": os.environ.get("DUCKDB_PATH"),
        },
        "dataset": {
            "benign": args.samples_per_class,
            "malicious": args.samples_per_class,
        },
        "methods": {
            "pramana_l1": {
                "any_flag_metrics": pramana_any_flag_metrics,
                "routing_gate_metrics": pramana_routing_metrics,
                "attack_breakdown": attack_breakdown,
                "mean_score": sum(pramana_scores) / len(pramana_scores),
            },
            "baseline_threshold_only": {
                "metrics": base_a_metrics,
                "mean_score": sum(base_a_scores) / len(base_a_scores),
            },
            "baseline_single_judge_proxy": {
                "metrics": base_b_metrics,
                "mean_score": sum(base_b_scores) / len(base_b_scores),
            },
        },
    }

    if args.verbose:
        report["debug"] = pramana_debug

    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote evaluation artifact: {output_path}")

    if not args.keep_workdir:
        # Keep artifact only; remove temporary DB files.
        for filename in ("eval.sqlite3", "eval.duckdb"):
            candidate = work_dir / filename
            if candidate.exists():
                candidate.unlink(missing_ok=True)
        try:
            work_dir.rmdir()
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
