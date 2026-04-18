import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jwt
import requests

from core.config.settings import get_settings
from scripts.testnet_config import (
    METRICS_POLL_INTERVAL_S,
    MONITOR_TOTAL_DURATION_S,
    PRAMANA_API_URL,
    PRAMANA_ORG_ID,
)


class MetricsCollector:
    def __init__(self, output_dir: str = "outputs"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.metrics_file = self.output_dir / "metrics.csv"
        self.traces_file = self.output_dir / "traces.json"
        self.fieldnames = [
            "timestamp",
            "org_id",
            "agent_addr",
            "action",
            "flag_count",
            "risk_score",
            "council_action",
            "l1_agents",
            "injection_score",
            "halluc_score",
            "drift_score",
            "network",
        ]
        self.traces = []
        self._init_csv()

    def _init_csv(self):
        with open(self.metrics_file, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=self.fieldnames).writeheader()

    @staticmethod
    def _auth_headers(org_id: str) -> dict[str, str]:
        s = get_settings()
        token = jwt.encode({"org_id": org_id}, s.JWT_SECRET, algorithm="HS256")
        return {
            "Authorization": f"Bearer {token}",
            "X-Org-Id": org_id,
        }

    def collect_metrics(self, api_url: str, org_id: str) -> dict:
        try:
            resp = requests.get(
                f"{api_url}/traces",
                headers=self._auth_headers(org_id),
                params={"limit": 50},
                timeout=10,
            )
            if resp.status_code != 200:
                return {}

            data = resp.json().get("data", [])
            metrics = {}

            for trace in data:
                agent_addr = trace.get("agent_addr", "unknown")
                if agent_addr not in metrics:
                    metrics[agent_addr] = {
                        "trace_count": 0,
                        "flag_count": 0,
                        "avg_risk": 0.0,
                        "last_action": "",
                        "council_actions": [],
                    }

                metrics[agent_addr]["trace_count"] += 1
                metrics[agent_addr]["flag_count"] += trace.get("flag_count", 0)
                risk_score = trace.get("risk_score", 0.0)
                metrics[agent_addr]["avg_risk"] = (
                    metrics[agent_addr]["avg_risk"]
                    * (metrics[agent_addr]["trace_count"] - 1)
                    + risk_score
                ) / metrics[agent_addr]["trace_count"]
                council_action = trace.get("council_action", "PASS")
                if council_action not in metrics[agent_addr]["council_actions"]:
                    metrics[agent_addr]["council_actions"].append(council_action)

            return metrics
        except Exception as e:
            print(f"Metrics collection failed: {e}")
            return {}

    def record_metrics(self, metrics: dict, org_id: str):
        timestamp = datetime.now(timezone.utc).isoformat()
        with open(self.metrics_file, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.fieldnames)
            for agent_addr, m in metrics.items():
                writer.writerow(
                    {
                        "timestamp": timestamp,
                        "org_id": org_id,
                        "agent_addr": agent_addr,
                        "action": m.get("last_action", "unknown"),
                        "flag_count": m.get("flag_count", 0),
                        "risk_score": m.get("avg_risk", 0.0),
                        "council_action": ",".join(m.get("council_actions", [])),
                        "l1_agents": 0,
                        "injection_score": 0.0,
                        "halluc_score": 0.0,
                        "drift_score": 0.0,
                        "network": "base",
                    }
                )

    def record_traces(self, traces: list):
        self.traces.extend(traces)
        with open(self.traces_file, "w") as f:
            json.dump(self.traces, f, indent=2)

    def get_summary(self) -> dict:
        return {
            "metrics_file": str(self.metrics_file),
            "traces_file": str(self.traces_file),
            "trace_count": len(self.traces),
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default=PRAMANA_API_URL)
    parser.add_argument("--org", default=PRAMANA_ORG_ID)
    parser.add_argument("--interval", type=int, default=METRICS_POLL_INTERVAL_S)
    parser.add_argument("--duration", type=int, default=MONITOR_TOTAL_DURATION_S)
    parser.add_argument("--output-dir", default="outputs")
    args = parser.parse_args()

    collector = MetricsCollector(args.output_dir)
    start_time = time.time()
    poll_count = 0

    print(
        f"Monitoring {args.api_url} (org={args.org}) for {args.duration}s, polling every {args.interval}s"
    )

    while time.time() - start_time < args.duration:
        metrics = collector.collect_metrics(args.api_url, args.org)
        if metrics:
            collector.record_metrics(metrics, args.org)
            poll_count += 1
            print(f"[{poll_count}] {datetime.now()}: {len(metrics)} agents")
        else:
            print(f"[{poll_count}] {datetime.now()}: no data")

        time.sleep(args.interval)

    summary = collector.get_summary()
    print(f"\nMonitoring complete. Summary: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
