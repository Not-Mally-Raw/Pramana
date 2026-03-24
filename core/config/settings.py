"""
core/config/settings.py — All configuration, loaded from environment + .env file.
No hardcoding. Dynamic: settings reload on SIGHUP or via API endpoint.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent.parent.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── App ──────────────────────────────────────────────────────────────────
    APP_NAME:    str = "Pramana"
    APP_VERSION: str = "1.0.0"
    DEBUG:       bool = False
    LOG_LEVEL:   str = "INFO"

    # ── Network ──────────────────────────────────────────────────────────────
    NETWORK:           str = "anvil"         # anvil | sepolia | base
    ANVIL_RPC:         str = "http://127.0.0.1:8545"
    ALCHEMY_WS_URL:    str = ""              # wss://eth-sepolia.g.alchemy.com/v2/KEY
    ALCHEMY_HTTP_URL:  str = ""
    CHAIN_ID_ANVIL:    int = 31337
    CHAIN_ID_SEPOLIA:  int = 11155111

    # ── Contracts ────────────────────────────────────────────────────────────
    ORCHESTRATOR_ADDR: str = ""
    GUARDIAN_ADDR:     str = ""
    SBT_ADDR:          str = ""
    DEPLOYER_KEY:      str = ""              # private key for testnet deploys

    # ── LLM ──────────────────────────────────────────────────────────────────
    GROQ_API_KEY:       str = ""
    GROQ_MODEL_L1:      str = "mixtral-8x7b-32768"        # fast, free tier
    GROQ_MODEL_COUNCIL: str = "llama-3.3-70b-versatile"   # council voting
    GROQ_MAX_TOKENS:    int = 512
    GROQ_TIMEOUT_S:     float = 8.0
    COUNCIL_DELAY_S:    float = 2.0        # delay between sequential council calls

    # ── Databases ────────────────────────────────────────────────────────────
    SQLITE_PATH:  str = "data/pramana.db"
    DUCKDB_PATH:  str = "data/analytics.duckdb"
    CHROMA_PATH:  str = "data/chroma"
    CHROMA_COLLECTION: str = "agent_traces"

    # ── Thresholds ────────────────────────────────────────────────────────────
    ESCALATION_FLAG_THRESHOLD: int   = 2      # flags >= this → escalate
    RISK_SCORE_PAUSE:          float = 0.70   # auto-pause above this
    RISK_SCORE_ESCALATE:       float = 0.40
    HALLUC_SCORE_THRESHOLD:    float = 0.80   # below this → flag
    DRIFT_SIMILARITY_THRESHOLD: float = 0.70  # below this → flag
    BALANCE_DRAIN_PCT:         float = 0.10   # >10% drain → flag
    ACTION_LOOP_PER_MIN:       int   = 5      # >5 actions/min → flag
    GAS_SPIKE_MULTIPLIER:      float = 2.0    # gas > 2x avg → flag
    RISK_UNCERTAINTY_PENALTY:  float = 0.10   # add risk when signals are skipped/missing
    RISK_FLAG_DENSITY_BONUS:   float = 0.20   # max additive bonus from multi-signal agreement

    # ── Agent Profile Thresholds (Step 1) ────────────────────────────────────
    AGENT_PROFILE_THRESHOLDS: dict = {
        "treasury_bot": {
            "ACTION_LOOP_PER_MIN": 5,
            "BALANCE_DRAIN_PCT": 0.05,
            "RISK_SCORE_PAUSE": 0.60,
            "GAS_SPIKE_MULTIPLIER": 1.5,   # Fix 11: new
            "DRIFT_WINDOW_TXS": 10,        # Fix 11: new
        },
        "keeper_bot": {
            "ACTION_LOOP_PER_MIN": 10,
            "BALANCE_DRAIN_PCT": 0.10,
            "RISK_SCORE_PAUSE": 0.70,
            "GAS_SPIKE_MULTIPLIER": 2.0,   # Fix 11: new
            "DRIFT_WINDOW_TXS": 20,        # Fix 11: new
        },
        "arb_bot": {
            "ACTION_LOOP_PER_MIN": 60,     # Fix 11: was 30, too low for real arb
            "BALANCE_DRAIN_PCT": 0.20,
            "RISK_SCORE_PAUSE": 0.80,
            "GAS_SPIKE_MULTIPLIER": 4.0,   # Fix 11: new — arb bots bid aggressively
            "DRIFT_WINDOW_TXS": 100,       # Fix 11: new — 30+ actions/min needs wide window
        },
        "rebalancer": {
            "ACTION_LOOP_PER_MIN": 15,
            "BALANCE_DRAIN_PCT": 0.15,
            "RISK_SCORE_PAUSE": 0.75,
            "GAS_SPIKE_MULTIPLIER": 2.5,   # Fix 11: new
            "DRIFT_WINDOW_TXS": 30,        # Fix 11: new
        },
        "market_maker": {                  # Fix 11: new profile
            "ACTION_LOOP_PER_MIN": 200,
            "BALANCE_DRAIN_PCT": 0.60,
            "RISK_SCORE_PAUSE": 0.85,
            "GAS_SPIKE_MULTIPLIER": 3.0,
            "DRIFT_WINDOW_TXS": 200,
        },
        "generic": {
            "ACTION_LOOP_PER_MIN": 5,
            "BALANCE_DRAIN_PCT": 0.10,
            "RISK_SCORE_PAUSE": 0.70,
            "GAS_SPIKE_MULTIPLIER": 2.0,   # Fix 11: new
            "DRIFT_WINDOW_TXS": 10,        # Fix 11: new
        },
    }

    # ── Guard policy ────────────────────────────────────────────────────────
    GUARD_BLOCK_THRESHOLD:     float = 0.70   # score >= threshold blocks action
    GUARD_FAIL_MODE:           str = "open"   # open | closed

    # ── Flow / Scheduler ─────────────────────────────────────────────────────
    POLL_INTERVAL_S:  int = 30               # APScheduler polling interval
    MAX_RETRIES:      int = 3                # circuit breaker threshold
    RETRY_BASE_DELAY: float = 1.0            # exponential backoff base
    LISTENER_ENABLED:  bool = False           # start chain listener at API boot

    # ── Dashboard ────────────────────────────────────────────────────────────
    DASHBOARD_PORT: int = 8501
    API_PORT:       int = 8000
    CORS_ORIGINS:   str = "http://localhost:8501"

    # ── Alerts ───────────────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID:   str = ""
    DISCORD_WEBHOOK:    str = ""

    # ── Multi-tenancy ────────────────────────────────────────────────────────
    DEFAULT_ORG_ID: str = "default"

    # ── Derived properties ────────────────────────────────────────────────────
    @property
    def active_rpc(self) -> str:
        if self.NETWORK == "anvil":
            return self.ANVIL_RPC
        return self.ALCHEMY_HTTP_URL or self.ANVIL_RPC

    @property
    def active_chain_id(self) -> int:
        return self.CHAIN_ID_SEPOLIA if self.NETWORK == "sepolia" else self.CHAIN_ID_ANVIL

    @property
    def is_testnet(self) -> bool:
        return self.NETWORK in ("anvil", "sepolia")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Single cached Settings instance. Use invalidate_settings() to reload."""
    return Settings()


def invalidate_settings() -> None:
    """Force reload on next get_settings() call."""
    get_settings.cache_clear()


def monitor_settings_path() -> Path:
    return Path(__file__).parent / "monitor_settings.json"


def default_monitor_settings() -> dict[str, Any]:
    return {
        "application_insights": "pramana-insights",
        "continuous_evaluation_enabled": True,
        "continuous_sample_rate_per_hour": 6,
        "scheduled_evaluations_enabled": True,
        "evaluators": ["Groundedness", "Relevance", "Task-Adherence", "Fluency", "Violence"],
        "scheduled_red_teaming_enabled": False,
        "red_team_issues": ["task_adherence", "prohibited_actions"],
        "evaluation_alerts_enabled": True,
        "judge_model": "groq-llama-3.3-70b",
        "economic_sanity_threshold": 0.75,
        "hallucination_drift_window_seconds": 900,
        "fail_closed_mode": {
            "injection": False,
            "hallucination": False,
            "network": False,
            "operations": False,
            "recovery": False,
        },
    }


def load_monitor_settings() -> dict[str, Any]:
    path = monitor_settings_path()
    if not path.exists():
        return default_monitor_settings()
    try:
        return {**default_monitor_settings(), **json.loads(path.read_text(encoding="utf-8"))}
    except Exception:
        return default_monitor_settings()


def save_monitor_settings(payload: dict[str, Any]) -> None:
    path = monitor_settings_path()
    merged = {**default_monitor_settings(), **payload}
    path.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")
