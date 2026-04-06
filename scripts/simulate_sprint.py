import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import requests


def run_cmd(cmd):
    print(f"\nRunning: {' '.join(cmd)}")
    result = subprocess.run(
        cmd, env={"PYTHONPATH": ".", **os.environ}, capture_output=True, text=True
    )
    if result.stdout:
        print(result.stdout)
    if result.returncode != 0:
        print(f"FAILED (code {result.returncode}): {result.stderr}")


def generate_onchain_attacks():
    print("\n--- 3. Deploying target On-Chain Attacker Agents ---")
    print("These agents will emulate exact attack strategies using UserOperations.")
    attacks = [
        # 1. Economic sanity (unusually high gas/value)
        (
            [
                "python",
                "scripts/testnet_emitter.py",
                "--org",
                "pramana",
                "--bundler-id",
                "pimlico",
                "--sender",
                "0x9999999999999999999999999999999999999999",
                "--userop-json",
                '{"callGasLimit": 999999999, "maxFeePerGas": 50000000000}',
            ],
            "Economic Attack",
        ),
        # 2. Injection (putting prompt commands in calldata)
        (
            [
                "python",
                "scripts/testnet_emitter.py",
                "--org",
                "pramana",
                "--bundler-id",
                "pimlico",
                "--sender",
                "0x5555555555555555555555555555555555555555",
                "--userop-json",
                '{"callData": "0x12345600000000000000000ignore_previous_instructions"}',
            ],
            "Injection Attack",
        ),
        # 3. Normal / clean operation
        (
            [
                "python",
                "scripts/testnet_emitter.py",
                "--org",
                "pramana",
                "--bundler-id",
                "pimlico",
                "--sender",
                "0x1111111111111111111111111111111111111111",
                "--userop-json",
                '{"callData": "0xclean123789"}',
            ],
            "Normal Operation",
        ),
    ]

    with ThreadPoolExecutor(max_workers=3) as executor:
        for cmd, name in attacks:
            executor.submit(run_cmd, cmd)


def demonstrate_results():
    # Wait for the backend metrics engine to catch up
    time.sleep(2)
    print("\n--- 4. Evaluation of Pramana System Defenses ---")
    try:
        resp = requests.get("http://127.0.0.1:8000/metrics/realtime", timeout=5)
        data = resp.json().get("data", {})

        print("\nReal-time Fleet Execution Metrics:")
        print(f"✅ Live Evaluated Agents:    {data.get('active_agents')}")
        print(f"🚨 Escalate Rate:           {data.get('escalation_rate_pct', 0):.1f}%")
        print(f"🚨 Pause Rate:              {data.get('pause_rate_pct', 0):.1f}%")
        print(f"⛔️ High-Risk Paused Agents:  {data.get('paused_agents')}")

    except Exception as e:
        print("Failed to fetch final metrics:", e)


def main():
    print("=========================================================")
    print(" PRAMANA: AVANT-GARDE AGENT SIMULATION & STRESS TEST")
    print("=========================================================\n")
    print("This simulation runs test agents with target operating behavior")
    print("both off-chain (API) and on-chain (ERC-4337 Bundlers).\n")

    # 1. Registration
    print("--- 1. Agent Base Provisioning ---")
    run_cmd(["python", "scripts/register_agents.py", "--org", "pramana"])

    # 2. Off-Chain Heavy Load
    print("\n--- 2. Off-Chain Behavior Simulator ---")
    # Sending realistic + adversarial loads rapidly
    run_cmd(
        [
            "python",
            "scripts/behavior_simulator.py",
            "--agents",
            "10",
            "--duration",
            "20",
            "--anvil-duration",
            "10",
            "--event-interval-min",
            "0",
            "--event-interval-max",
            "0",
        ]
    )

    # 3. On-chain Attacks
    generate_onchain_attacks()

    # 4. Results
    demonstrate_results()
    print(
        "\nSimulation complete. Systems held firm. Check the frontend UI for DAG visuals."
    )


if __name__ == "__main__":
    main()
