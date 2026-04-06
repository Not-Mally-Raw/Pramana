import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

import requests

from scripts.testnet_config import PRAMANA_API_URL, PRAMANA_ORG_ID


class ResultsAnalyzer:
    def __init__(self, metrics_file: str, settings_file: Optional[str] = None):
        self.metrics_file = Path(metrics_file)
        self.settings_file = Path(settings_file) if settings_file else None
        self.metrics = []
        self.load_metrics()

        self.risk_score_pause_threshold = 0.70
        if self.settings_file and self.settings_file.exists():
            self._load_thresholds()

    def load_metrics(self):
        try:
            with open(self.metrics_file) as f:
                reader = csv.DictReader(f)
                self.metrics = list(reader)
        except Exception as e:
            print(f"Error loading metrics: {e}")
            self.metrics = []

    def _load_thresholds(self):
        try:
            with open(self.settings_file) as f:
                config = json.load(f)
                self.risk_score_pause_threshold = config.get("RISK_SCORE_PAUSE", 0.70)
        except Exception:
            pass

    def compute_guard_pause_percentage(self) -> float:
        if not self.metrics:
            return 0.0

        paused_count = 0
        for metric in self.metrics:
            try:
                risk_score = float(metric.get("risk_score", 0.0))
                if risk_score >= self.risk_score_pause_threshold:
                    paused_count += 1
            except (ValueError, TypeError):
                pass

        return (paused_count / len(self.metrics)) * 100.0 if self.metrics else 0.0

    def compute_agent_statistics(self) -> dict:
        stats = defaultdict(
            lambda: {
                "event_count": 0,
                "flag_count": 0,
                "avg_risk": 0.0,
                "max_risk": 0.0,
                "pause_count": 0,
                "council_actions": defaultdict(int),
            }
        )

        for metric in self.metrics:
            agent = metric.get("agent_addr", "unknown")
            stats[agent]["event_count"] += 1

            try:
                flag_count = int(metric.get("flag_count", 0))
                stats[agent]["flag_count"] += flag_count
            except (ValueError, TypeError):
                pass

            try:
                risk = float(metric.get("risk_score", 0.0))
                if metric in self.metrics:
                    idx = self.metrics.index(metric)
                    if idx > 0:
                        prev_risk = float(self.metrics[idx - 1].get("risk_score", 0.0))
                        stats[agent]["avg_risk"] = (
                            stats[agent]["avg_risk"] * (idx) + risk
                        ) / (idx + 1)
                stats[agent]["max_risk"] = max(stats[agent]["max_risk"], risk)
                if risk >= self.risk_score_pause_threshold:
                    stats[agent]["pause_count"] += 1
            except (ValueError, TypeError):
                pass

            council_action = metric.get("council_action", "PASS")
            for action in council_action.split(","):
                if action.strip():
                    stats[agent]["council_actions"][action.strip()] += 1

        return {addr: dict(data) for addr, data in stats.items()}

    def compute_detection_summary(self) -> dict:
        guard_pause_pct = self.compute_guard_pause_percentage()
        agent_stats = self.compute_agent_statistics()

        total_events = len(self.metrics)
        paused_events = sum(
            1
            for m in self.metrics
            if float(m.get("risk_score", 0.0)) >= self.risk_score_pause_threshold
        )

        return {
            "total_events": total_events,
            "paused_by_guard": paused_events,
            "guard_pause_percentage": guard_pause_pct,
            "agent_statistics": agent_stats,
            "high_risk_threshold": self.risk_score_pause_threshold,
        }

    def generate_report(self, output_file: str = "outputs/testnet_report.txt"):
        summary = self.compute_detection_summary()
        agent_stats = summary.get("agent_statistics", {})

        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w") as f:
            f.write("PRAMANA TESTNET PILOT REPORT\n")
            f.write("=" * 80 + "\n\n")

            f.write(f"EXECUTION METRICS\n")
            f.write(f"-" * 80 + "\n")
            f.write(f"Total Events Processed: {summary['total_events']}\n")
            f.write(f"Events Paused by Guard: {summary['paused_by_guard']}\n")
            f.write(
                f"Guard Pause Percentage: {summary['guard_pause_percentage']:.2f}%\n"
            )
            f.write(
                f"Pause Threshold (Risk Score): {summary['high_risk_threshold']}\n\n"
            )

            f.write(f"AGENT-LEVEL STATISTICS\n")
            f.write(f"-" * 80 + "\n")
            for agent_addr, stats in sorted(agent_stats.items()):
                f.write(f"\n{agent_addr}:\n")
                f.write(f"  Events: {stats['event_count']}\n")
                f.write(
                    f"  Flags: {stats['flag_count']} ({(stats['flag_count']/max(stats['event_count'],1)*100):.1f}%)\n"
                )
                f.write(f"  Avg Risk Score: {stats['avg_risk']:.3f}\n")
                f.write(f"  Max Risk Score: {stats['max_risk']:.3f}\n")
                f.write(f"  Paused Events: {stats['pause_count']}\n")
                if stats["council_actions"]:
                    f.write(f"  Council Decisions: {dict(stats['council_actions'])}\n")

            f.write(f"\n{'=' * 80}\n")
            f.write(f"VALIDATION SUMMARY\n")
            f.write(f"{'=' * 80}\n\n")

            realistic_agents = [
                "0x1111111111111111111111111111111111111111",
                "0x2222222222222222222222222222222222222222",
                "0x3333333333333333333333333333333333333333",
                "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            ]
            adversarial_agents = [
                "0x4444444444444444444444444444444444444444",
                "0x5555555555555555555555555555555555555555",
                "0x6666666666666666666666666666666666666666",
                "0x7777777777777777777777777777777777777777",
                "0x8888888888888888888888888888888888888888",
                "0x9999999999999999999999999999999999999999",
            ]

            realistic_flags = sum(
                agent_stats.get(a, {}).get("flag_count", 0) for a in realistic_agents
            )
            adversarial_flags = sum(
                agent_stats.get(a, {}).get("flag_count", 0) for a in adversarial_agents
            )

            f.write(f"Realistic Agents (Expected No Flags):\n")
            f.write(f"  Total Flags: {realistic_flags}\n")
            f.write(
                f"  Status: {'PASS' if realistic_flags < 5 else 'FAIL'} (target: <5 false positives)\n\n"
            )

            f.write(f"Adversarial Agents (Expected Flags):\n")
            f.write(f"  Total Flags: {adversarial_flags}\n")
            f.write(
                f"  Status: {'PASS' if adversarial_flags > 20 else 'FAIL'} (target: >20 detections)\n\n"
            )

            f.write(f"Overall Guard Effectiveness:\n")
            f.write(f"  Pause Rate: {summary['guard_pause_percentage']:.2f}%\n")
            f.write(
                f"  Status: {'PASS' if 5 <= summary['guard_pause_percentage'] <= 40 else 'FAIL'}\n"
            )
            f.write(
                f"  (target: 5-40% pause rate, avoiding false positive lock-in)\n\n"
            )

        print(f"Report written to {output_path}")
        print(f"Guard Pause Percentage: {summary['guard_pause_percentage']:.2f}%")
        return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics-file", default="outputs/metrics.csv")
    parser.add_argument("--settings-file", default=None)
    parser.add_argument("--output", default="outputs/testnet_report.txt")
    args = parser.parse_args()

    analyzer = ResultsAnalyzer(args.metrics_file, args.settings_file)
    summary = analyzer.generate_report(args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
