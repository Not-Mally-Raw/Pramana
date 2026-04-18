"""scripts/testnet_emitter.py — end-to-end bundler loop probe.

Usage examples:
  python scripts/testnet_emitter.py --org test_org --bundler-id test_bundler
  python scripts/testnet_emitter.py --org test_org --bundler-id pimlico --bundler-rpc https://... --attempt-send

This script always runs `/bundler/validate` first, then optionally attempts
live `eth_sendUserOperation` against a configured bundler RPC.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jwt
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config.settings import get_settings
from core.db import get_sqlite_conn, init_sqlite
from scripts.testnet_config import PRAMANA_ORG_ID


@dataclass
class LoopOutcome:
    validate_status: str
    decision: str
    risk_score: float
    attestation_nonce: str
    user_op_hash: str
    bundler_send_attempted: bool
    bundler_send_ok: bool
    bundler_send_result: str
    confirm_ok: bool
    confirm_result: str


def _default_user_operation(sender: str) -> dict[str, Any]:
    return {
        "version": "v0.6",
        "sender": sender,
        "nonce": 1,
        "initCode": "0x",
        "callData": "0x",
        "callGasLimit": 50000,
        "verificationGasLimit": 100000,
        "preVerificationGas": 21000,
        "maxFeePerGas": 1_000_000_000,
        "maxPriorityFeePerGas": 1_000_000_000,
        "paymasterAndData": "0x",
        "signature": "0xabc",
    }


def _ensure_registry_rows(sender: str, org_id: str, bundler_id: str) -> None:
    init_sqlite()
    with get_sqlite_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO bundler_registry
                            (bundler_url, org_id, fallback_policy)
                        VALUES (?, ?, 'ESCALATE')
            """,
            (bundler_id, org_id),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO agents
              (agent_addr, org_id, did, agent_profile, registered_at, updated_at)
            VALUES (?, ?, '', 'generic', datetime('now'), datetime('now'))
            """,
            (sender, org_id),
        )


def _rpc_call(rpc_url: str, method: str, params: list[Any]) -> dict[str, Any]:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }
    resp = requests.post(rpc_url, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def run(args: argparse.Namespace) -> LoopOutcome:
    global get_settings
    settings = get_settings()
    jwt_secret = args.jwt_secret or settings.JWT_SECRET
    token = args.jwt
    if not token:
        try:
            import jwt

            token = jwt.encode(
                {"org_id": args.org}, settings.JWT_SECRET, algorithm="HS256"
            )
        except Exception:
            token = jwt.encode({"org_id": args.org}, jwt_secret, algorithm="HS256")

    _ensure_registry_rows(
        sender=args.sender, org_id=args.org, bundler_id=args.bundler_id
    )

    user_op = _default_user_operation(sender=args.sender)
    if args.userop_json:
        user_op.update(json.loads(args.userop_json))

    headers = {
        "Authorization": f"Bearer {token}",
        "x-bundler-id": args.bundler_id,
        "x-chain-id": str(args.chain_id),
        "x-entry-point": args.entry_point,
        "Content-Type": "application/json",
    }

    validate_url = f"{args.api.rstrip('/')}/bundler/validate"
    validate_resp = requests.post(
        validate_url, json={"userOperation": user_op}, headers=headers, timeout=20
    )
    validate_resp.raise_for_status()
    validate_payload = validate_resp.json()

    status = str(validate_payload.get("status", ""))
    data = (
        validate_payload.get("data", {}) if isinstance(validate_payload, dict) else {}
    )
    att = data.get("attestation", {}) if isinstance(data, dict) else {}
    att_payload = att.get("payload", {}) if isinstance(att, dict) else {}

    decision = str(att_payload.get("decision", data.get("decision", "")))
    risk_score = float(
        att_payload.get("risk_score", data.get("risk_score", 0.0)) or 0.0
    )
    attestation_nonce = str(att_payload.get("nonce", ""))
    user_op_hash = str(att_payload.get("user_op_hash", data.get("user_op_hash", "")))

    bundler_send_attempted = False
    bundler_send_ok = False
    bundler_send_result = "not attempted"
    confirm_ok = False
    confirm_result = "not attempted"

    if args.attempt_send and args.bundler_rpc:
        bundler_send_attempted = True
        try:
            supported = _rpc_call(args.bundler_rpc, "eth_supportedEntryPoints", [])
            supported_eps = supported.get("result", [])
            if isinstance(supported_eps, list) and supported_eps:
                print(f"supported entry points: {supported_eps}")

            send = _rpc_call(
                args.bundler_rpc, "eth_sendUserOperation", [user_op, args.entry_point]
            )
            if "result" in send:
                bundler_send_ok = True
                bundler_send_result = str(send["result"])
            else:
                bundler_send_result = str(send.get("error", "unknown bundler error"))

            if bundler_send_ok and attestation_nonce:
                confirm_url = f"{args.api.rstrip('/')}/bundler/confirm"
                confirm_resp = requests.post(
                    confirm_url,
                    json={
                        "attestation_nonce": attestation_nonce,
                        "tx_hash": bundler_send_result,
                    },
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    timeout=20,
                )
                confirm_resp.raise_for_status()
                confirm_json = confirm_resp.json()
                confirm_ok = confirm_json.get("status") == "ok"
                confirm_result = json.dumps(
                    confirm_json.get("data", {}), sort_keys=True
                )
        except Exception as exc:
            bundler_send_result = f"bundler send failed: {type(exc).__name__}: {exc}"

    return LoopOutcome(
        validate_status=status,
        decision=decision,
        risk_score=risk_score,
        attestation_nonce=attestation_nonce,
        user_op_hash=user_op_hash,
        bundler_send_attempted=bundler_send_attempted,
        bundler_send_ok=bundler_send_ok,
        bundler_send_result=bundler_send_result,
        confirm_ok=confirm_ok,
        confirm_result=confirm_result,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Pramana bundler loop probe")
    parser.add_argument(
        "--api", default="http://127.0.0.1:8000", help="Pramana API base URL"
    )
    parser.add_argument(
        "--org", default=PRAMANA_ORG_ID, help="Organization id for JWT claim"
    )
    parser.add_argument(
        "--sender",
        default="0x1234567890123456789012345678901234567890",
        help="UserOperation sender",
    )
    parser.add_argument(
        "--bundler-id",
        default="test_bundler",
        help="Bundler id passed via x-bundler-id",
    )
    parser.add_argument("--chain-id", type=int, default=31337, help="Target chain id")
    parser.add_argument(
        "--entry-point",
        default="0x5FF137D4b0FDCD49DcA30c7CF57E578a026d2789",
        help="ERC-4337 entry point address",
    )
    parser.add_argument("--jwt", default="", help="Optional pre-built JWT bearer token")
    parser.add_argument(
        "--jwt-secret",
        default="",
        help="Optional JWT secret for local token generation",
    )
    parser.add_argument(
        "--bundler-rpc",
        default="",
        help="Bundler RPC URL (enables real eth_sendUserOperation attempt)",
    )
    parser.add_argument(
        "--attempt-send", action="store_true", help="Attempt live bundler RPC send"
    )
    parser.add_argument(
        "--userop-json",
        default="",
        help="JSON object merged into the default UserOperation",
    )
    args = parser.parse_args()

    try:
        outcome = run(args)
        print(json.dumps(outcome.__dict__, indent=2, sort_keys=True))
        if outcome.validate_status != "ok":
            return 2
        return 0
    except Exception as exc:
        print(f"bundler loop failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
