"""
core/config/settings.py — All configuration, loaded from environment + .env file.
No hardcoding. Dynamic: settings reload on SIGHUP or via API endpoint.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

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

    # ── Flow / Scheduler ─────────────────────────────────────────────────────
    POLL_INTERVAL_S:  int = 30               # APScheduler polling interval
    MAX_RETRIES:      int = 3                # circuit breaker threshold
    RETRY_BASE_DELAY: float = 1.0            # exponential backoff base

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
