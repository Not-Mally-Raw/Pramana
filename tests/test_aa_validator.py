import pytest
import time
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from core.app import app
from utils.models import UserOperation
from core.db import get_sqlite_conn, init_sqlite

client = TestClient(app)

# Dummy test entry point and default JWT bypass org_id for testing
VALID_ENTRY_POINT = "0x5FF137D4b0FDCD49DcA30c7CF57E578a026d2789"
INVALID_ENTRY_POINT = "0x1111111111111111111111111111111111111111"

@pytest.fixture(autouse=True)
def setup_db():
    from core.config.settings import get_settings
    import os
    s = get_settings()
    s.SQLITE_PATH = "/tmp/test_pramana.db"
    
    # Wipe it fresh on every test setup
    if os.path.exists(s.SQLITE_PATH):
        os.remove(s.SQLITE_PATH)

    
    init_sqlite()
    with get_sqlite_conn() as conn:
        # Register test bundler with PASS fallback for testing
        conn.execute("INSERT OR IGNORE INTO bundler_registry (bundler_url, org_id, fallback_policy) VALUES ('test_bundler', 'test_org', 'PASS')")
        
        # Register the dummy agent so PramanaFlow doesn't crash on agent lookup
        conn.execute(
            "INSERT OR IGNORE INTO agents (agent_addr, org_id, agent_profile, registered_at) VALUES (?, ?, ?, datetime('now'))",
            ("0x1234567890123456789012345678901234567890", "test_org", "treasury_bot")
        )

def make_user_op(overrides: dict = None) -> dict:
    base = {
        "sender": "0x1234567890123456789012345678901234567890",
        "nonce": 1,
        "callData": "0x",
        "callGasLimit": 50000,
        "verificationGasLimit": 100000,
        "preVerificationGas": 21000,
        "maxFeePerGas": 1000000000,
        "maxPriorityFeePerGas": 1000000000,
        "signature": "0xabc",
        "paymasterAndData": "0x"
    }
    if overrides:
        base.update(overrides)
    return base


@patch("core.agents.aa_validator.AAValidatorAgent._check_call_graph_depth", return_value=1)
def test_happy_path(mock_depth):
    # A perfectly normal UserOp using JWT override header for testing (handled by testclient if we stub)
    # Actually, we rely on the injected x-org-id fallback in app.py for test requests without real JWT
    # Wait, the app.py middleware enforces JWT but we can mock settings or generate a real JWT.
    
    from core.config.settings import get_settings
    import jwt
    s = get_settings()
    token = jwt.encode({"org_id": "test_org"}, s.JWT_SECRET, algorithm="HS256")
    headers = {"Authorization": f"Bearer {token}", "x-bundler-id": "test_bundler"}

    # We need to simulate the state where EntryPoint is in the event metadata.
    # But since UserOp payload doesn't include EntryPoint directly (it's passed by bundler param), 
    # our AAValidatorAgent currently looks for it in `state.event.metadata["entry_point"]` which our adapter doesn't set yet.
    # If not set, it defaults to None and passes the canonical check safely.

    uop = make_user_op()
    resp = client.post("/bundler/validate", json={"userOperation": uop}, headers=headers)
    
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "attestation" in data["data"]
    att = data["data"]["attestation"]
    
    # Happy path should PASS or ESCALATE depending on L1 checks 
    # (assuming no other L1 agent flags it - empty calldata is safe)
    assert att["payload"]["decision"] in ["PASS", "ESCALATE"] 


@patch("core.agents.aa_validator.CANONICAL_ENTRY_POINTS", {VALID_ENTRY_POINT})
def test_non_canonical_entry_point():
    from core.config.settings import get_settings
    import jwt
    s = get_settings()
    token = jwt.encode({"org_id": "test_org"}, s.JWT_SECRET, algorithm="HS256")
    headers = {"Authorization": f"Bearer {token}", "x-bundler-id": "test_bundler"}
    
    # To test non-canonical, we need the adapter or agent to see an invalid EP.
    # Since adapter currently ignores EP, we mock the `check` method directly for this test case
    # or just test the check() logic directly.
    from core.agents.aa_validator import AAValidatorAgent
    from utils.models import ChainEvent, PramanaState
    from core.parsers.userop import UserOperationParser

    uop = UserOperationParser.parse(make_user_op())
    event = UserOperationParser.adapt_to_chain_event(uop)
    # Hack the metadata for the check
    event.metadata = {"entry_point": INVALID_ENTRY_POINT}
    state = PramanaState(event=event)
    
    agent = AAValidatorAgent()
    result = agent.check(event, state)
    
    assert result.flagged is True
    assert result.score >= 0.85
    assert "non-canonical" in result.reason


def test_nonce_lane_parallelism():
    from core.agents.aa_validator import AAValidatorAgent
    
    # Mock the DB query to return 15 parallel ops for this lane
    with patch("core.agents.aa_validator.AAValidatorAgent._check_nonce_lane_parallelism", return_value=15):
        from utils.models import ChainEvent, PramanaState
        from core.parsers.userop import UserOperationParser

        uop = UserOperationParser.parse(make_user_op({"nonce": (5 << 64) | 1})) # key=5
        event = UserOperationParser.adapt_to_chain_event(uop)
        
        # Test Treasury Profile (Limit 1)
        state_treasury = PramanaState(event=event, agent_profile="treasury_bot")
        agent = AAValidatorAgent()
        res_treasury = agent.check(event, state_treasury)
        assert res_treasury.flagged is True
        assert res_treasury.score >= 0.85
        assert "nonce lane abuse" in res_treasury.reason


def test_gas_budget_inflation():
    from core.agents.aa_validator import AAValidatorAgent
    
    # 5x inflation 
    with patch("core.agents.aa_validator.AAValidatorAgent._check_gas_budget_inflation", return_value=5.0):
        from utils.models import ChainEvent, PramanaState
        from core.parsers.userop import UserOperationParser

        uop = UserOperationParser.parse(make_user_op())
        event = UserOperationParser.adapt_to_chain_event(uop)
        state = PramanaState(event=event)
        
        agent = AAValidatorAgent()
        res = agent.check(event, state)
        assert res.flagged is True
        assert res.score >= 0.80
        assert "gas budget inflation" in res.reason


def test_replay_protection():
    from core.config.settings import get_settings
    import jwt
    s = get_settings()
    token = jwt.encode({"org_id": "test_org"}, s.JWT_SECRET, algorithm="HS256")
    headers = {"Authorization": f"Bearer {token}", "x-bundler-id": "test_bundler"}

    # Unique payload for test
    uop = make_user_op({"callData": "0xdeadbeef"})
    
    # Submit 1st time
    resp1 = client.post("/bundler/validate", json={"userOperation": uop}, headers=headers)
    assert resp1.status_code == 200
    
    # Simulate on-chain inclusion
    from core.attestation import AttestationService
    user_op_hash = resp1.json()["data"]["attestation"]["payload"]["user_op_hash"]
    AttestationService.mark_redeemed(user_op_hash)
    
    # Submitting same payload again triggers Replay Protection immediately
    resp2 = client.post("/bundler/validate", json={"userOperation": uop}, headers=headers)
    
    # Our API rescues exceptions into a clean JSON layout
    data = resp2.json()
    assert data["status"] == "error"
    assert "Replay Attempt" in data["data"]["reason"] or "already evaluated" in data["data"]["reason"]
    assert data["data"]["decision"] == "PAUSE"
