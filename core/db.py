"""
core/db.py — SQLite (state/events) + DuckDB (analytics) setup.
All tables include org_id for multi-tenancy. LRU eviction to 10K rows per org.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import duckdb

from core.config.settings import get_settings

__all__ = [
    "get_sqlite_conn",
    "get_duck_conn",
    "init_sqlite",
    "init_duckdb",
    "evict_old_rows",
    "upsert_agent",
    "insert_event",
    "insert_trace",
    "upsert_policy",
    "get_routing_table",
    "upsert_routing_rule",
    "insert_agent_metric",
    "insert_council_decision",
    "insert_lifecycle_transition",
]

# ── SQLite helpers ────────────────────────────────────────────────────────────

_SQLITE_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS agents (
    agent_addr   TEXT NOT NULL,
    org_id       TEXT NOT NULL,
    did          TEXT,
    agent_profile TEXT DEFAULT 'generic',
    risk_tier    INTEGER DEFAULT 0,
    paused       INTEGER DEFAULT 0,
    reputation   REAL    DEFAULT 0.0,
    registered_at TEXT,
    updated_at   TEXT,
    metadata     TEXT,
    PRIMARY KEY (agent_addr, org_id)
);

CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id       TEXT    NOT NULL,
    agent_addr   TEXT    NOT NULL,
    tx_hash      TEXT,
    action       TEXT,
    calldata     TEXT,
    value_wei    TEXT,
    gas_price    TEXT,
    network      TEXT,
    created_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS traces (
    flow_id      TEXT    PRIMARY KEY,
    org_id       TEXT    NOT NULL,
    agent_addr   TEXT    NOT NULL,
    trace_hash   TEXT,
    dag_json     TEXT,
    flag_count   INTEGER DEFAULT 0,
    risk_score   REAL    DEFAULT 0.0,
    council_action TEXT,
    created_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS policies (
    policy_id    TEXT    PRIMARY KEY,
    org_id       TEXT    NOT NULL,
    agent_addr   TEXT    NOT NULL,
    rules_json   TEXT    NOT NULL,
    rule_hash    TEXT,
    version      INTEGER DEFAULT 1,
    active       INTEGER DEFAULT 1,
    created_at   TEXT,
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS routing_table (
    rule_key     TEXT    PRIMARY KEY,
    condition    TEXT    NOT NULL,
    action       TEXT    NOT NULL,
    priority     INTEGER DEFAULT 5,
    enabled      INTEGER DEFAULT 1,
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS reputation_scores (
    agent_addr     TEXT NOT NULL,
    org_id         TEXT NOT NULL,
    success_rate   REAL DEFAULT 0.0,
    roi_score      REAL DEFAULT 0.0,
    uptime_score   REAL DEFAULT 0.0,
    halluc_rate    REAL DEFAULT 0.0,
    composite      REAL DEFAULT 0.0,
    tx_count       INTEGER DEFAULT 0,
    updated_at     TEXT,
    PRIMARY KEY (agent_addr, org_id)
);

CREATE TABLE IF NOT EXISTS sanctions_list (
    address      TEXT PRIMARY KEY,
    source       TEXT,
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS bundler_registry (
    bundler_url       TEXT PRIMARY KEY,
    org_id            TEXT NOT NULL,
    active            INTEGER DEFAULT 1,
    fallback_policy   TEXT DEFAULT 'ESCALATE',
    supported_chains  TEXT,
    webhook_url       TEXT,
    registered_at     TEXT
);

CREATE TABLE IF NOT EXISTS user_operations (
    user_op_hash             TEXT PRIMARY KEY,
    sender                   TEXT NOT NULL,
    entry_point              TEXT,
    paymaster                TEXT,
    nonce_key                INTEGER,
    nonce_sequence           INTEGER,
    call_gas_limit           INTEGER,
    verification_gas_limit   INTEGER,
    pre_verification_gas     INTEGER,
    decision                 TEXT,
    risk_score               REAL,
    trace_hash               TEXT,
    bundler_id               TEXT,
    chain_id                 INTEGER,
    attestation_nonce        TEXT,
    attestation_expires      INTEGER,
    created_at               TEXT
);

CREATE TABLE IF NOT EXISTS attestation_log (
    user_op_hash       TEXT PRIMARY KEY,
    attestation_nonce  TEXT NOT NULL,
    nonce              TEXT,
    decision           TEXT,
    risk_score         REAL,
    trace_hash         TEXT,
    bundler_id         TEXT,
    redeemed           INTEGER DEFAULT 0,
    expires_at         INTEGER,
    expires_at_block   INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT
);

CREATE TABLE IF NOT EXISTS lifecycle_transitions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_addr            TEXT NOT NULL,
    org_id                TEXT NOT NULL,
    from_state            TEXT NOT NULL,
    to_state              TEXT NOT NULL,
    reason                TEXT,
    capital_at_risk_usd   REAL,
    created_at            TEXT
);

CREATE TABLE IF NOT EXISTS known_contracts (
    address            TEXT PRIMARY KEY,
    chain_id           INTEGER NOT NULL,
    deployed_block     INTEGER NOT NULL,
    verified_at        TEXT
);

CREATE TABLE IF NOT EXISTS open_positions (
    position_id TEXT PRIMARY KEY,
    agent_addr TEXT NOT NULL,
    org_id TEXT NOT NULL,
    protocol TEXT NOT NULL,
    position_type TEXT NOT NULL,
    asset TEXT NOT NULL,
    amount_wei TEXT NOT NULL,
    usd_value_at_entry REAL,
    current_usd_value REAL,
    health_factor REAL,
    liquidation_threshold REAL,
    chain_id INTEGER NOT NULL,
    block_entered INTEGER,
    last_updated_block INTEGER,
    last_updated_ts INTEGER,
    is_finalized INTEGER DEFAULT 0,
    is_closed INTEGER DEFAULT 0,
    closed_ts INTEGER
);

CREATE TABLE IF NOT EXISTS recovery_alerts (
    alert_id TEXT PRIMARY KEY,
    agent_addr TEXT NOT NULL,
    org_id TEXT NOT NULL,
    triggered_by_trace TEXT,
    pause_timestamp INTEGER,
    classifications_json TEXT,
    total_usd_at_risk REAL,
    critical_count INTEGER DEFAULT 0,
    warning_count INTEGER DEFAULT 0,
    safe_count INTEGER DEFAULT 0,
    webhook_delivered INTEGER DEFAULT 0,
    webhook_ts INTEGER,
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS rate_limit_windows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id TEXT NOT NULL,
    ts REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_org    ON events(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_agent  ON events(agent_addr, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_agent_org_ts ON events(agent_addr, org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_traces_org    ON traces(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_traces_hash   ON traces(trace_hash);
CREATE INDEX IF NOT EXISTS idx_policies_org  ON policies(org_id, agent_addr);
CREATE INDEX IF NOT EXISTS idx_userops_sender_nonce ON user_operations(sender, nonce_key);
CREATE INDEX IF NOT EXISTS idx_positions_agent_org ON open_positions(agent_addr, org_id, is_closed);
CREATE INDEX IF NOT EXISTS idx_rate_limit_org_ts ON rate_limit_windows(org_id, ts);
"""


def _ensure_dir(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)

def is_sanctioned(address: str) -> bool:
    """O(1) local sanctions check."""
    if not address:
        return False
    try:
        with get_sqlite_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM sanctions_list WHERE lower(address) = ?",
                (address.lower(),)
            ).fetchone()
        return bool(row)
    except Exception:
        return False


def init_sqlite() -> None:
    s = get_settings()
    _ensure_dir(s.SQLITE_PATH)
    with sqlite3.connect(s.SQLITE_PATH) as conn:
        conn.executescript(_SQLITE_SCHEMA)
        _apply_sqlite_migrations(conn)
        _seed_routing_rules(conn)


def _has_column(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(r[1] == column_name for r in rows)


def _apply_sqlite_migrations(conn: sqlite3.Connection) -> None:
    """Apply additive schema migrations to existing local databases."""
    migrations: list[tuple[str, str, str]] = [
        ("agents", "agent_profile", "TEXT DEFAULT 'generic'"),
        ("lifecycle_transitions", "capital_at_risk_usd", "REAL"),
        ("open_positions", "is_finalized", "INTEGER DEFAULT 0"),
        ("bundler_registry", "active", "INTEGER DEFAULT 1"),
        ("bundler_registry", "supported_chains", "TEXT"),
        ("bundler_registry", "webhook_url", "TEXT"),
        ("bundler_registry", "registered_at", "TEXT"),
        ("attestation_log", "nonce", "TEXT"),
        ("attestation_log", "decision", "TEXT"),
        ("attestation_log", "risk_score", "REAL"),
        ("attestation_log", "trace_hash", "TEXT"),
        ("attestation_log", "bundler_id", "TEXT"),
        ("attestation_log", "expires_at", "INTEGER"),
        ("attestation_log", "expires_at_block", "INTEGER NOT NULL DEFAULT 0"),
        ("attestation_log", "created_at", "TEXT"),
    ]

    for table_name, column_name, column_def in migrations:
        if not _has_column(conn, table_name, column_name):
            conn.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}"
            )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_attestation_expires_block ON attestation_log(expires_at_block)"
    )


def _seed_routing_rules(conn: sqlite3.Connection) -> None:
    """Seed routing_table from config file on first init (idempotent)."""
    try:
        row = conn.execute("SELECT COUNT(*) FROM routing_table").fetchone()
        if row and int(row[0]) > 0:
            return

        rules_path = Path(__file__).parent / "config" / "routing_rules.json"
        if not rules_path.exists():
            return

        data = json.loads(rules_path.read_text(encoding="utf-8"))
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()

        for section in ("escalation_rules", "pass_rules"):
            for rule_key, rule in (data.get(section, {}) or {}).items():
                conn.execute(
                    """
                    INSERT OR IGNORE INTO routing_table (rule_key, condition, action, priority, enabled, updated_at)
                    VALUES (?, ?, ?, ?, 1, ?)
                    """,
                    (
                        rule_key,
                        str(rule.get("condition", "")),
                        str(rule.get("action", "pass")),
                        int(rule.get("priority", 5)),
                        now,
                    ),
                )
    except Exception:
        # Startup should not fail if seed config is malformed.
        pass


@contextmanager
def get_sqlite_conn() -> Generator[sqlite3.Connection, None, None]:
    s = get_settings()
    _ensure_dir(s.SQLITE_PATH)
    conn = sqlite3.connect(s.SQLITE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def evict_old_rows(org_id: str, max_rows: int = 10_000) -> None:
    """Keep at most max_rows per org in events/traces to control disk usage."""
    with get_sqlite_conn() as conn:
        for table in ("events", "traces"):
            conn.execute(
                f"""
                DELETE FROM {table}
                WHERE org_id = ?
                  AND rowid NOT IN (
                    SELECT rowid FROM {table}
                    WHERE org_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                  )
                """,
                (org_id, org_id, max_rows),
            )


# ── CRUD helpers ──────────────────────────────────────────────────────────────

def upsert_agent(
    agent_addr: str,
    org_id: str,
    did: str = "",
    agent_profile: str = "generic",
    risk_tier: int = 0,
    paused: bool = False,
    metadata: dict | None = None,
) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with get_sqlite_conn() as conn:
        conn.execute(
            """
            INSERT INTO agents (agent_addr, org_id, did, agent_profile, risk_tier, paused, metadata, registered_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_addr, org_id) DO UPDATE SET
                did=excluded.did, agent_profile=excluded.agent_profile, risk_tier=excluded.risk_tier,
                paused=excluded.paused, metadata=excluded.metadata, updated_at=excluded.updated_at
            """,
            (agent_addr, org_id, did, agent_profile, risk_tier, int(paused), json.dumps(metadata or {}), now, now),
        )


def get_agent_profile(agent_addr: str, org_id: str) -> str:
    """Fetch agent's profile, default to 'generic'."""
    with get_sqlite_conn() as conn:
        row = conn.execute(
            "SELECT agent_profile FROM agents WHERE agent_addr=? AND org_id=?",
            (agent_addr, org_id)
        ).fetchone()
    return row[0] if row else "generic"


def insert_event(event_dict: dict) -> None:
    from datetime import datetime, timezone
    with get_sqlite_conn() as conn:
        conn.execute(
            """
            INSERT INTO events (org_id, agent_addr, tx_hash, action, calldata, value_wei, gas_price, network, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_dict.get("org_id", "default"),
                event_dict.get("agent_id", ""),
                event_dict.get("tx_hash", ""),
                event_dict.get("action", ""),
                event_dict.get("calldata", ""),
                str(event_dict.get("value_wei", 0)),
                str(event_dict.get("gas_price", 0)),
                event_dict.get("network", "anvil"),
                event_dict.get("timestamp", datetime.now(timezone.utc).isoformat()),
            ),
        )


def insert_trace(
    flow_id: str,
    org_id: str,
    agent_addr: str,
    trace_hash: str,
    dag_json: str,
    flag_count: int,
    risk_score: float,
    council_action: str,
) -> None:
    from datetime import datetime, timezone
    with get_sqlite_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO traces
              (flow_id, org_id, agent_addr, trace_hash, dag_json, flag_count, risk_score, council_action, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (flow_id, org_id, agent_addr, trace_hash, dag_json, flag_count, risk_score,
             council_action, datetime.now(timezone.utc).isoformat()),
        )


def insert_lifecycle_transition(
    agent_addr: str,
    org_id: str,
    from_state: str,
    to_state: str,
    reason: str = "",
    capital_at_risk_usd: float | None = None,
) -> None:
    from datetime import datetime, timezone

    with get_sqlite_conn() as conn:
        conn.execute(
            """
            INSERT INTO lifecycle_transitions
              (agent_addr, org_id, from_state, to_state, reason, capital_at_risk_usd, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                agent_addr,
                org_id,
                from_state,
                to_state,
                reason,
                capital_at_risk_usd,
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def upsert_policy(
    policy_id: str,
    org_id: str,
    agent_addr: str,
    rules_json: str,
    rule_hash: str,
    version: int = 1,
) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with get_sqlite_conn() as conn:
        conn.execute(
            """
            INSERT INTO policies (policy_id, org_id, agent_addr, rules_json, rule_hash, version, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(policy_id) DO UPDATE SET
                rules_json=excluded.rules_json, rule_hash=excluded.rule_hash,
                version=excluded.version, updated_at=excluded.updated_at
            """,
            (policy_id, org_id, agent_addr, rules_json, rule_hash, version, now, now),
        )


def get_routing_table() -> dict:
    """Return {rule_key: {condition, action, priority}} dict for O(1) routing."""
    with get_sqlite_conn() as conn:
        rows = conn.execute(
            "SELECT rule_key, condition, action, priority FROM routing_table WHERE enabled=1 ORDER BY priority DESC"
        ).fetchall()
    return {r["rule_key"]: {"condition": r["condition"], "action": r["action"], "priority": r["priority"]} for r in rows}


def upsert_routing_rule(rule_key: str, condition: str, action: str, priority: int = 5) -> None:
    from datetime import datetime, timezone
    with get_sqlite_conn() as conn:
        conn.execute(
            """
            INSERT INTO routing_table (rule_key, condition, action, priority, enabled, updated_at)
            VALUES (?, ?, ?, ?, 1, ?)
            ON CONFLICT(rule_key) DO UPDATE SET
                condition=excluded.condition, action=excluded.action,
                priority=excluded.priority, updated_at=excluded.updated_at
            """,
            (rule_key, condition, action, priority, datetime.now(timezone.utc).isoformat()),
        )


# ── DuckDB helpers ────────────────────────────────────────────────────────────

_DUCKDB_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_metrics (
    agent_addr   VARCHAR NOT NULL,
    org_id       VARCHAR NOT NULL,
    window_ts    TIMESTAMP NOT NULL,
    tx_count     INTEGER,
    flag_count   INTEGER,
    avg_gas_gwei DOUBLE,
    avg_risk     DOUBLE,
    success_pct  DOUBLE,
    PRIMARY KEY (agent_addr, org_id, window_ts)
);

CREATE TABLE IF NOT EXISTS reputation_history (
    agent_addr   VARCHAR NOT NULL,
    org_id       VARCHAR NOT NULL,
    recorded_at  TIMESTAMP NOT NULL,
    composite    DOUBLE,
    success_rate DOUBLE,
    roi_score    DOUBLE,
    uptime_score DOUBLE,
    halluc_rate  DOUBLE
);

CREATE TABLE IF NOT EXISTS council_decisions (
    flow_id      VARCHAR PRIMARY KEY,
    agent_addr   VARCHAR,
    org_id       VARCHAR,
    decided_at   TIMESTAMP,
    approved     BOOLEAN,
    risk_score   DOUBLE,
    confidence   DOUBLE,
    action       VARCHAR,
    reason       VARCHAR
);
"""


def init_duckdb() -> None:
    s = get_settings()
    _ensure_dir(s.DUCKDB_PATH)
    with duckdb.connect(s.DUCKDB_PATH) as conn:
        conn.execute(_DUCKDB_SCHEMA)


@contextmanager
def get_duck_conn() -> Generator[duckdb.DuckDBPyConnection, None, None]:
    s = get_settings()
    _ensure_dir(s.DUCKDB_PATH)
    conn = duckdb.connect(s.DUCKDB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def query_reputation_window(agent_addr: str, org_id: str, days: int = 30) -> list[dict]:
    with get_duck_conn() as conn:
        rows = conn.execute(
            """
            SELECT recorded_at, composite, success_rate, halluc_rate
            FROM reputation_history
            WHERE agent_addr = ? AND org_id = ?
              AND recorded_at >= now() - INTERVAL (?) DAY
            ORDER BY recorded_at DESC
            """,
            [agent_addr, org_id, days],
        ).fetchall()
    return [{"recorded_at": r[0], "composite": r[1], "success_rate": r[2], "halluc_rate": r[3]} for r in rows]


def insert_agent_metric(
    agent_addr: str,
    org_id: str,
    risk_score: float,
    flag_count: int,
    avg_gas_gwei: float,
    success_pct: float,
) -> None:
    """Upsert minute-window metrics for analytics/reputation feedback loop."""
    from datetime import datetime, timezone

    window_ts = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    with get_duck_conn() as conn:
        current = conn.execute(
            """
            SELECT tx_count, flag_count, avg_gas_gwei, avg_risk, success_pct
            FROM agent_metrics WHERE agent_addr=? AND org_id=? AND window_ts=?
            """,
            [agent_addr, org_id, window_ts],
        ).fetchone()

        if not current:
            conn.execute(
                """
                INSERT INTO agent_metrics
                  (agent_addr, org_id, window_ts, tx_count, flag_count, avg_gas_gwei, avg_risk, success_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    agent_addr,
                    org_id,
                    window_ts,
                    1,
                    int(flag_count),
                    float(avg_gas_gwei),
                    float(risk_score),
                    max(0.0, min(float(success_pct), 1.0)),
                ],
            )
            return

        old_tx, old_flags, old_gas, old_risk, old_success = current
        old_tx = int(old_tx or 0)
        new_tx = old_tx + 1

        def _avg(old_val: float, new_val: float) -> float:
            return ((float(old_val or 0.0) * old_tx) + float(new_val)) / max(new_tx, 1)

        conn.execute(
            """
            UPDATE agent_metrics
            SET tx_count=?,
                flag_count=?,
                avg_gas_gwei=?,
                avg_risk=?,
                success_pct=?
            WHERE agent_addr=? AND org_id=? AND window_ts=?
            """,
            [
                new_tx,
                int(old_flags or 0) + int(flag_count),
                _avg(float(old_gas or 0.0), float(avg_gas_gwei)),
                _avg(float(old_risk or 0.0), float(risk_score)),
                _avg(float(old_success or 0.0), max(0.0, min(float(success_pct), 1.0))),
                agent_addr,
                org_id,
                window_ts,
            ],
        )


def insert_council_decision(
    flow_id: str,
    agent_addr: str,
    org_id: str,
    approved: bool,
    risk_score: float,
    confidence: float,
    action: str,
    reason: str,
) -> None:
    from datetime import datetime, timezone

    with get_duck_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO council_decisions
              (flow_id, agent_addr, org_id, decided_at, approved, risk_score, confidence, action, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                flow_id,
                agent_addr,
                org_id,
                datetime.now(timezone.utc),
                bool(approved),
                float(risk_score),
                float(confidence),
                str(action),
                str(reason)[:512],
            ],
        )
