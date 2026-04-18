#!/usr/bin/env python3
"""Run the corrected Pramana validation gate end-to-end.

This script codifies the practical validation checklist into one reproducible command.
It verifies API contracts, runs baseline detection evaluation, runs the fleet simulation,
and executes the bundler validation loop probe.

Example:
    PYTHONPATH=. python scripts/validation/run_validation_gate.py \
      --api-url http://127.0.0.1:8000 \
      --org test_pilot
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import jwt
import requests
import websockets

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config.settings import get_settings
from scripts.testnet_config import PRAMANA_ORG_ID


@dataclass
class StepResult:
    name: str
    ok: bool
    duration_s: float
    details: dict[str, Any] = field(default_factory=dict)
    error: str = ""


def _token_for_org(org_id: str) -> str:
    settings = get_settings()
    return jwt.encode({"org_id": org_id}, settings.JWT_SECRET, algorithm="HS256")


def _headers(org_id: str) -> dict[str, str]:
    token = _token_for_org(org_id)
    return {
        "Authorization": f"Bearer {token}",
        "X-Org-Id": org_id,
        "Content-Type": "application/json",
    }


def _run_cmd(cmd: list[str], timeout_s: int = 600) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    if existing_pythonpath:
        env["PYTHONPATH"] = f"{PROJECT_ROOT}:{existing_pythonpath}"
    else:
        env["PYTHONPATH"] = str(PROJECT_ROOT)

    return subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )


def _extract_json_from_output(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if not candidate:
        raise ValueError("empty output")

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    first = candidate.find("{")
    last = candidate.rfind("}")
    if first == -1 or last == -1 or last <= first:
        raise ValueError("no JSON object found in output")

    parsed = json.loads(candidate[first : last + 1])
    if not isinstance(parsed, dict):
        raise ValueError("parsed JSON is not an object")
    return parsed


async def _ws_ping(api_url: str, org_id: str, token: str) -> dict[str, Any]:
    if api_url.startswith("https://"):
        ws_base = "wss://" + api_url[len("https://") :]
    elif api_url.startswith("http://"):
        ws_base = "ws://" + api_url[len("http://") :]
    else:
        ws_base = api_url

    query = urlencode({"org_id": org_id, "since": 0})
    ws_url = f"{ws_base.rstrip('/')}/ws/stream?{query}"

    ws_headers = {
        "Authorization": f"Bearer {token}",
        "X-Org-Id": org_id,
    }
    async with websockets.connect(
        ws_url,
        additional_headers=ws_headers,
        open_timeout=8,
        close_timeout=5,
    ) as socket:
        await socket.send("ping")
        raw = await asyncio.wait_for(socket.recv(), timeout=8)

    payload = json.loads(raw)
    if payload.get("type") != "pong":
        raise ValueError(f"unexpected websocket payload: {payload}")
    return payload


def _validate_api_contracts(api_url: str, org_id: str) -> StepResult:
    name = "api_contracts"
    started = time.perf_counter()
    details: dict[str, Any] = {}

    try:
        token = _token_for_org(org_id)
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Org-Id": org_id,
            "Content-Type": "application/json",
        }

        health = requests.get(f"{api_url.rstrip('/')}/health", timeout=8)
        health.raise_for_status()
        health_json = health.json()
        if health_json.get("status") != "ok":
            raise ValueError(f"/health status not ok: {health_json}")
        details["health"] = health_json

        agent_addr = "0x" + secrets.token_hex(20)
        reg_payload = {
            "agent_addr": agent_addr,
            "did": f"did:pramana:{agent_addr}",
            "agent_profile": "generic",
            "metadata": {"source": "validation_gate"},
        }
        reg = requests.post(
            f"{api_url.rstrip('/')}/agents/register",
            headers=headers,
            json=reg_payload,
            timeout=10,
        )
        reg.raise_for_status()
        reg_json = reg.json()
        if reg_json.get("status") not in {"created", "ok"}:
            raise ValueError(f"/agents/register unexpected status: {reg_json}")
        details["register"] = {
            "status": reg_json.get("status"),
            "agent_addr": reg_json.get("data", {}).get("agent_addr"),
        }

        ingest_payload = {
            "event": {
                "agent_id": agent_addr,
                "org_id": org_id,
                "tx_hash": "0x" + secrets.token_hex(32),
                "action": "transfer",
                "calldata": json.dumps({"to": "0x1234567890123456789012345678901234567890", "value": "1"}),
                "value_wei": 1,
                "gas_price": 1_000_000_000,
                "network": "anvil",
                "metadata": {"source": "validation_gate"},
            },
            "org_id": org_id,
            "simulation": True,
        }
        ingest = requests.post(
            f"{api_url.rstrip('/')}/ingest",
            headers=headers,
            json=ingest_payload,
            timeout=30,
        )
        ingest.raise_for_status()
        ingest_json = ingest.json()

        data = ingest_json.get("data", {}) if isinstance(ingest_json, dict) else {}
        if not isinstance(data, dict):
            raise ValueError("/ingest response data is not an object")

        action = data.get("action")
        if action is None:
            raise ValueError("/ingest data.action missing")

        details["ingest"] = {
            "status": ingest_json.get("status"),
            "action": action,
            "risk_score": data.get("risk_score"),
            "trace_hash": data.get("trace_hash"),
        }

        ws_payload = asyncio.run(_ws_ping(api_url=api_url, org_id=org_id, token=token))
        details["ws_ping"] = ws_payload

        return StepResult(
            name=name,
            ok=True,
            duration_s=time.perf_counter() - started,
            details=details,
        )
    except Exception as exc:
        return StepResult(
            name=name,
            ok=False,
            duration_s=time.perf_counter() - started,
            details=details,
            error=f"{type(exc).__name__}: {exc}",
        )


def _run_pytest_gate(pytest_targets: list[str]) -> StepResult:
    name = "pytest_gate"
    started = time.perf_counter()

    cmd = [sys.executable, "-m", "pytest", "-q", *pytest_targets]
    result = _run_cmd(cmd, timeout_s=1200)

    details = {
        "cmd": cmd,
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-2000:],
    }

    return StepResult(
        name=name,
        ok=(result.returncode == 0),
        duration_s=time.perf_counter() - started,
        details=details,
        error="" if result.returncode == 0 else "pytest returned non-zero",
    )


def _run_baseline_eval(samples_per_class: int, seed: int, output_path: Path) -> StepResult:
    name = "baseline_eval"
    started = time.perf_counter()

    cmd = [
        sys.executable,
        "scripts/evaluate_detection_baselines.py",
        "--samples-per-class",
        str(samples_per_class),
        "--seed",
        str(seed),
        "--output",
        str(output_path),
    ]
    result = _run_cmd(cmd, timeout_s=1800)

    details: dict[str, Any] = {
        "cmd": cmd,
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-5000:],
        "stderr_tail": result.stderr[-2000:],
        "artifact": str(output_path),
    }

    if result.returncode != 0:
        return StepResult(
            name=name,
            ok=False,
            duration_s=time.perf_counter() - started,
            details=details,
            error="baseline evaluation command failed",
        )

    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        routing = payload["pramana_routing_gate"]
        any_flag = payload["pramana_any_flag"]

        details["pramana_routing_gate"] = {
            "precision": routing.get("precision"),
            "recall": routing.get("recall"),
            "fpr": routing.get("fpr"),
            "accuracy": routing.get("accuracy"),
        }
        details["pramana_any_flag"] = {
            "precision": any_flag.get("precision"),
            "recall": any_flag.get("recall"),
            "fpr": any_flag.get("fpr"),
            "accuracy": any_flag.get("accuracy"),
        }

        return StepResult(
            name=name,
            ok=True,
            duration_s=time.perf_counter() - started,
            details=details,
        )
    except Exception as exc:
        return StepResult(
            name=name,
            ok=False,
            duration_s=time.perf_counter() - started,
            details=details,
            error=f"failed to parse baseline artifact: {type(exc).__name__}: {exc}",
        )


def _run_fleet_eval(
    api_url: str,
    org_id: str,
    actions: int,
    delay_s: float,
    summary_path: Path,
) -> StepResult:
    name = "fleet_eval"
    started = time.perf_counter()

    cmd = [
        sys.executable,
        "scripts/agents/fleet_runner.py",
        "--api-url",
        api_url,
        "--org",
        org_id,
        "--mode",
        "sequential",
        "--actions",
        str(actions),
        "--delay",
        str(delay_s),
        "--summary-json",
        str(summary_path),
    ]
    result = _run_cmd(cmd, timeout_s=1800)

    details: dict[str, Any] = {
        "cmd": cmd,
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-5000:],
        "stderr_tail": result.stderr[-2000:],
        "summary": str(summary_path),
    }

    if result.returncode != 0:
        return StepResult(
            name=name,
            ok=False,
            duration_s=time.perf_counter() - started,
            details=details,
            error="fleet runner command failed",
        )

    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        totals = payload.get("totals", {})
        details["totals"] = totals

        actions_taken = int(totals.get("actions", 0) or 0)
        pass_count = int(totals.get("PASS", 0) or 0)
        escalate_count = int(totals.get("ESCALATE", 0) or 0)
        pause_count = int(totals.get("PAUSE", 0) or 0)

        if actions_taken <= 0:
            raise ValueError("fleet summary reported zero actions")
        if pass_count <= 0:
            raise ValueError("fleet summary reported zero PASS decisions")
        if (escalate_count + pause_count) <= 0:
            raise ValueError("fleet summary reported zero adversarial catches (ESCALATE/PAUSE)")

        return StepResult(
            name=name,
            ok=True,
            duration_s=time.perf_counter() - started,
            details=details,
        )
    except Exception as exc:
        return StepResult(
            name=name,
            ok=False,
            duration_s=time.perf_counter() - started,
            details=details,
            error=f"failed to validate fleet summary: {type(exc).__name__}: {exc}",
        )


def _run_bundler_probe(
    api_url: str,
    org_id: str,
    bundler_id: str,
    bundler_rpc: str,
    attempt_send: bool,
) -> StepResult:
    name = "bundler_probe"
    started = time.perf_counter()

    cmd = [
        sys.executable,
        "scripts/testnet_emitter.py",
        "--api",
        api_url,
        "--org",
        org_id,
        "--bundler-id",
        bundler_id,
    ]
    if bundler_rpc:
        cmd.extend(["--bundler-rpc", bundler_rpc])
        if attempt_send:
            cmd.append("--attempt-send")

    result = _run_cmd(cmd, timeout_s=1200)
    details: dict[str, Any] = {
        "cmd": cmd,
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-2000:],
    }

    if result.returncode != 0:
        return StepResult(
            name=name,
            ok=False,
            duration_s=time.perf_counter() - started,
            details=details,
            error="bundler probe command failed",
        )

    try:
        payload = _extract_json_from_output(result.stdout)

        required = {
            "validate_status",
            "decision",
            "risk_score",
            "attestation_nonce",
            "user_op_hash",
            "bundler_send_attempted",
            "bundler_send_ok",
            "bundler_send_result",
            "confirm_ok",
            "confirm_result",
        }
        missing = sorted(required - set(payload.keys()))
        if missing:
            raise ValueError(f"missing keys: {missing}")

        details["outcome"] = payload
        if str(payload.get("validate_status", "")).lower() != "ok":
            raise ValueError(f"validate_status is not ok: {payload.get('validate_status')}")

        return StepResult(
            name=name,
            ok=True,
            duration_s=time.perf_counter() - started,
            details=details,
        )
    except Exception as exc:
        return StepResult(
            name=name,
            ok=False,
            duration_s=time.perf_counter() - started,
            details=details,
            error=f"failed to parse bundler probe output: {type(exc).__name__}: {exc}",
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run corrected Pramana validation gate")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--org", default=PRAMANA_ORG_ID)

    parser.add_argument("--samples-per-class", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--fleet-actions", type=int, default=4)
    parser.add_argument("--fleet-delay", type=float, default=0.35)

    parser.add_argument("--bundler-id", default="test_bundler")
    parser.add_argument("--bundler-rpc", default="")
    parser.add_argument("--attempt-send", action="store_true")

    parser.add_argument(
        "--pytest-targets",
        nargs="*",
        default=["tests/smoke_test.py", "tests/test_flow.py", "tests/test_aa_validator.py"],
    )

    parser.add_argument(
        "--output-dir",
        default="outputs/validation_gate",
        help="Directory for artifacts and final report",
    )

    parser.add_argument("--skip-contracts", action="store_true")
    parser.add_argument("--skip-pytest", action="store_true")
    parser.add_argument("--skip-baseline", action="store_true")
    parser.add_argument("--skip-fleet", action="store_true")
    parser.add_argument("--skip-bundler", action="store_true")

    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config": {
            "api_url": args.api_url,
            "org": args.org,
            "samples_per_class": args.samples_per_class,
            "seed": args.seed,
            "fleet_actions": args.fleet_actions,
            "fleet_delay": args.fleet_delay,
            "bundler_id": args.bundler_id,
            "bundler_rpc": args.bundler_rpc,
            "attempt_send": args.attempt_send,
            "pytest_targets": args.pytest_targets,
        },
        "steps": [],
    }

    steps: list[StepResult] = []

    if not args.skip_contracts:
        print("[1/5] API contract checks...")
        steps.append(_validate_api_contracts(api_url=args.api_url, org_id=args.org))

    if not args.skip_pytest:
        print("[2/5] pytest gate...")
        steps.append(_run_pytest_gate(pytest_targets=args.pytest_targets))

    baseline_artifact = output_dir / "detection_eval.json"
    if not args.skip_baseline:
        print("[3/5] baseline evaluation...")
        steps.append(
            _run_baseline_eval(
                samples_per_class=args.samples_per_class,
                seed=args.seed,
                output_path=baseline_artifact,
            )
        )

    fleet_artifact = output_dir / "fleet_summary.json"
    if not args.skip_fleet:
        print("[4/5] fleet evaluation...")
        steps.append(
            _run_fleet_eval(
                api_url=args.api_url,
                org_id=args.org,
                actions=args.fleet_actions,
                delay_s=args.fleet_delay,
                summary_path=fleet_artifact,
            )
        )

    if not args.skip_bundler:
        print("[5/5] bundler loop probe...")
        steps.append(
            _run_bundler_probe(
                api_url=args.api_url,
                org_id=args.org,
                bundler_id=args.bundler_id,
                bundler_rpc=args.bundler_rpc,
                attempt_send=args.attempt_send,
            )
        )

    ok_count = 0
    for step in steps:
        report["steps"].append(asdict(step))
        status = "PASS" if step.ok else "FAIL"
        print(f"[{status}] {step.name} ({step.duration_s:.2f}s)")
        if not step.ok:
            print(f"       {step.error}")
        else:
            ok_count += 1

    report["summary"] = {
        "total_steps": len(steps),
        "passed_steps": ok_count,
        "failed_steps": len(steps) - ok_count,
        "ok": ok_count == len(steps),
    }

    report_path = output_dir / "validation_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"report={report_path}")

    return 0 if report["summary"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
