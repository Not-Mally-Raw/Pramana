"""
pramana_cli.py — Click CLI for Pramana.
Commands: init, run, deploy, bench, ingest, status
"""
from __future__ import annotations

import os
import sys

import click

sys.path.insert(0, os.path.dirname(__file__))


@click.group()
@click.version_option("1.0.0", prog_name="pramana")
def cli():
    """Pramana – On-Chain AgentOps Platform CLI"""


# ── init ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--org-id", default="default", help="Organization ID")
def init(org_id: str):
    """Initialize Pramana: create data/ dir, init DBs, create .env if missing."""
    import os
    from pathlib import Path

    click.echo("Initializing Pramana...")

    # Create data directory
    Path("data").mkdir(exist_ok=True)
    click.echo("  ✓ data/ directory created")

    # Init DBs
    os.environ.setdefault("SQLITE_PATH", "data/pramana.db")
    os.environ.setdefault("DUCKDB_PATH",  "data/analytics.duckdb")
    os.environ.setdefault("NETWORK", "anvil")
    try:
        from core.db import init_sqlite, init_duckdb
        init_sqlite()
        init_duckdb()
        click.echo("  ✓ SQLite + DuckDB initialized")
    except Exception as e:
        click.echo(f"  ⚠ DB init failed: {e}", err=True)

    # Create .env if missing
    env_path = Path(".env")
    if not env_path.exists():
        example = Path(".env.example")
        if example.exists():
            import shutil
            shutil.copy(example, env_path)
            click.echo("  ✓ .env created from .env.example (add your API keys)")
        else:
            env_path.write_text(f"GROQ_API_KEY=\nNETWORK=anvil\nDEFAULT_ORG_ID={org_id}\n")
            click.echo("  ✓ .env created (add your GROQ_API_KEY)")
    else:
        click.echo("  ✓ .env already exists")

    click.echo(f"\nPramana initialized for org '{org_id}'.")
    click.echo("  Next: pramana deploy  (deploy contracts to Anvil)")
    click.echo("        pramana run     (start API + listener)")


# ── run ───────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--host", default="0.0.0.0", help="API host")
@click.option("--port", default=8000,       help="API port")
@click.option("--workers", default=1,       help="Uvicorn workers")
@click.option("--no-listener", is_flag=True, help="Skip WebSocket listener")
def run(host: str, port: int, workers: int, no_listener: bool):
    """Start the Pramana API server (+ WebSocket listener)."""
    import subprocess, threading

    click.echo(f"Starting Pramana API on {host}:{port}...")

    if not no_listener:
        click.echo("  WebSocket listener will start after API is ready.")

    cmd = [
        sys.executable, "-m", "uvicorn",
        "core.app:app",
        "--host", host,
        "--port", str(port),
        "--workers", str(workers),
    ]
    subprocess.run(cmd)


# ── dashboard ─────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--port", default=8501, help="Streamlit port")
def dashboard(port: int):
    """Launch the Streamlit dashboard."""
    import subprocess
    cmd = [sys.executable, "-m", "streamlit", "run", "dashboard/app.py", "--server.port", str(port)]
    subprocess.run(cmd)


# ── deploy ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--rpc",    default="http://127.0.0.1:8545", help="RPC URL (Anvil default)")
@click.option("--verify", is_flag=True,                    help="Verify on Etherscan (Sepolia)")
def deploy(rpc: str, verify: bool):
    """Deploy Solidity contracts via Forge."""
    import subprocess

    click.echo(f"Deploying contracts to {rpc} ...")
    cmd = [
        "forge", "script", "contracts/script/Deploy.s.sol",
        "--rpc-url", rpc,
        "--broadcast",
    ]
    if verify:
        cmd += ["--verify"]

    result = subprocess.run(cmd, capture_output=True, text=True)
    click.echo(result.stdout)
    if result.returncode != 0:
        click.echo(result.stderr, err=True)
        click.echo("Deploy failed. Make sure Foundry is installed: curl -L https://foundry.paradigm.xyz | bash")
        sys.exit(1)
    else:
        click.echo("  ✓ Contracts deployed. Update .env with the contract addresses.")


# ── bench ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--n", default=100, help="Number of simulated transactions")
def bench(n: int):
    """Run the L1 pipeline latency benchmark."""
    from tests.benchmark_pipeline import run_benchmark
    run_benchmark(n)


# ── ingest ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--agent",     required=True, help="Agent address (0x...)")
@click.option("--action",    default="transfer")
@click.option("--calldata",  default="{}")
@click.option("--org-id",    default="default")
@click.option("--api-url",   default="http://localhost:8000")
def ingest(agent: str, action: str, calldata: str, org_id: str, api_url: str):
    """Manually inject a chain event into the flow."""
    import requests
    resp = requests.post(
        f"{api_url}/ingest",
        json={"agent_id": agent, "org_id": org_id, "action": action, "calldata": calldata},
        headers={"X-Org-Id": org_id},
        timeout=15,
    )
    if resp.status_code == 200:
        data = resp.json()["data"]
        click.echo(f"Flow result: {data.get('action')} | risk={data.get('risk_score',0):.2%} | flags={data.get('flag_count',0)}")
    else:
        click.echo(f"Error {resp.status_code}: {resp.text[:200]}", err=True)


# ── status ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--api-url", default="http://localhost:8000")
def status(api_url: str):
    """Check health of API, node, and databases."""
    import requests
    try:
        resp = requests.get(f"{api_url}/health/detailed", timeout=3)
        data = resp.json().get("data", {})
        click.echo("Pramana Status:")
        for k, v in data.items():
            icon = "✓" if v == "ok" else "✗"
            click.echo(f"  {icon} {k}: {v}")
    except Exception as e:
        click.echo(f"API unreachable: {e}", err=True)


if __name__ == "__main__":
    cli()
