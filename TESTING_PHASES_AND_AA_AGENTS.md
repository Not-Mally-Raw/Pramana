# Pramana Testing Phases & Journey to AA Agents Route

## Part 1: Current Testing Capabilities (Phase 0-1)

### What You Can Test RIGHT NOW

#### Phase 0: Unit Tests (Smoke)
```bash
cd /opt/anaconda3/pramana
python tests/smoke_test.py
```

**Covers** (11/11 tests):
- ✅ API health checks
- ✅ Agent registration via REST API
- ✅ Guard decisions (clean vs injected calldata)
- ✅ Event ingestion
- ✅ Trace retrieval
- ✅ Response format validation

**What it does NOT test**:
- ❌ Real blockchain interaction
- ❌ Real agent runtime behavior
- ❌ L2 Council voting
- ❌ On-chain emissions

**Time**: 30 seconds

---

#### Phase 1: Unit Tests (Pytest)
```bash
cd /opt/anaconda3/pramana
pytest tests/test_agents.py tests/test_flow.py -v
```

**Covers** (26/27 tests):
- ✅ InjectionAgent detects prompt injection patterns
- ✅ OperationsAgent detects action loops + balance drain
- ✅ NetworkAgent detects gas spikes + RPC failures
- ✅ ValidatorAgent schema checks
- ✅ DriftMonitorAgent baseline comparison
- ✅ L1 Crew coordination (parallel execution, hard-stop logic)
- ✅ Escalation routing (Pass → Escalate → Pause)
- ✅ L2 Council voting (3 judges with different temps)
- ✅ TraceDAG finalization + hashing
- ✅ Degraded mode fallbacks (when Groq fails)

**What it does NOT test**:
- ❌ Real blockchain state
- ❌ Real Groq API calls (mocked)
- ❌ Real smart contract emissions
- ❌ Multi-agent coordination beyond single event

**Time**: 2-3 minutes

---

## Part 2: Real Agent Testing Phases (Phase 2-2.5)

### Phase 2: Local Testnet Integration
**Blockers**: ⏳ **WAITING FOR STEP 2 (Financial Recovery) completion**

**What becomes possible**:
```python
# Test agent on local Anvil chain
anvil  # in terminal 1, keep running
python -m pytest tests/test_anvil_integration.py -v  # terminal 2
```

**Covers**:
- ✅ Deploy Orchestrator.sol contract to Anvil
- ✅ Register agent on-chain
- ✅ Submit ChainEvent from simulated agent
- ✅ Receive decision via contract event
- ✅ Verify TraceFinalized emission

**Timeline**: Step 2 (Financial Recovery) → Step 3 (RANSAC Baseline) → ✅ Phase 2 ready

---

### Phase 2.1: Single Agent Dry-Run
**Blockers**: ✅ **Ready after Phase 2**

**Example**:
```bash
# Run a single real agent (e.g., ArbitrageBot) on testnet without funds
python scripts/deploy_testnet_agent.py \
  --agent-type "arb_bot" \
  --network "sepolia" \
  --dry-run true  # No actual transactions
```

**What it does**:
1. Creates agent wallet (or uses existing)
2. Registers agent in Pramana
3. Simulates 100 transactions on testnet
4. Pramana checks each one
5. Reports: Decision rate, flag count, average latency
6. NO real funds moved (dry-run mode)

**Output**:
```
Test Results for ArbitrageBot:
├─ Registration: ✅ 0x1234...abcd
├─ Decisions received: 100/100 (100%)
├─ Avg latency: 420ms
├─ Flags from L1: 2 (both for high gas)
├─ L2 escalations: 0
└─ Confidence: 95% (ready for funded testnet)
```

---

### Phase 2.5: Multi-Agent LocalNet
**Blockers**: ✅ **Ready after Phase 2**

**Setup**:
```bash
# 1. Start local Anvil chain with 5 test agents
python scripts/spin_up_localnet.py --agents 5 --duration 1h

# 2. Each agent runs autonomously
# 3. Pramana monitors all 5 in parallel
# 4. Dashboard streams decisions in real-time
```

**Test Scenario**:
- **Agent 0** (Treasury Bot): Normal behavior (5 transactions/min) ✅ All PASS
- **Agent 1** (Arb Bot): High-frequency trading (30 tx/min) ✅ Profile-aware thresholds
- **Agent 2** (Keeper Bot): Recovery playbooks when paused ⏳ Waiting for Step 2
- **Agent 3** (Rebalancer): Multi-timescale drift detection ⏳ Waiting for Step 4
- **Agent 4** (Generic): Injection attack simulation ✅ Blocked at L1

**What you measure**:
- Pramana responsiveness (should be <500ms per event)
- False positive rate (good baseline agents should get 0 flags)
- Council agreement rate (3 judges should agree 95%+ on obvious cases)
- Trace auditability (can replay entire log from SQLite)

**Output**: `localnet_test_2h.json`
```json
{
  "agents": 5,
  "total_events": 45000,
  "events_processed": 45000,
  "decisions": {
    "PASS": 43200,
    "ESCALATE": 1500,
    "PAUSE": 300
  },
  "avg_latency_ms": 385,
  "false_positive_rate": 0.02,
  "true_positive_rate": 0.98
}
```

---

## Part 3: Real Agent Tests (Phase 3+)

### Phase 3: Sepolia Testnet (Live Agents, No Real Value)
Requires:
- ✅ All Steps 2-4 complete
- ✅ Sepolia deployment workflow
- 💾 1-2 realistic test agents

**Process**:
1. Deploy Orchestrator + Guardian + ReputationSBT to Sepolia
2. Deploy 2 test agents (e.g., ERC20 swap bot, staking monitor)
3. Fund agents with testnet ETH + stablecoins
4. Run for 24h with continuous Pramana monitoring
5. Verify: All decisions logged on-chain, no false pauses, council consensus

---

## Part 4: The AA Agents Route (Like OpenClaw)

### What is "AA Agents"?

**AA** = **Account Abstraction** (ERC-4337 standard)

**OpenClaw** (and similar) uses AA because:
1. **Abstracted execution**: Agent operations are bundled (single tx, multiple calls)
2. **Paymaster support**: Agents can transfer transaction costs (gasless if bundler pays)
3. **Policy enforcement**: User operation validation rules checked before bundler includes tx
4. **Atomicity**: Multiple operations succeed or all fail together

**Example**:
```solidity
// Traditional agent (EOA)
function swap() {
  USDC.transfer(0xDEX, amount);  // Tx 1
  DEX.execute(swap_data);         // Tx 2
  USDT.receive(output);           // Tx 3 (may fail mid-way)
}

// AA agent (via UserOperation)
UserOperation {
  sender: agent_address,
  callData: abi.encodeCall(SmartAccount.batch, [
    call(USDC.transfer, ..),
    call(DEX.execute, ..),
    call(USDT.receive, ..)
  ]),
  paymaster: address,  // Someone else pays gas
  signature: sign(agent_private_key)
}
// Bundler validates via policy, includes in block, all-or-nothing
```

---

### Why Does Pramana Need AA Support?

**Current (EOA) approach**:
- Pramana sees individual transactions after-the-fact
- Hard to enforce atomicity (if Tx 2 reverts, Tx 1 already executed)
- Costs high (N transactions = N gas fees)

**AA approach**:
- Pramana validates **before bundler includes** the UserOperation
- Can enforce "bundle this only if risk_score < 0.70"
- Single gas fee for entire bundle
- Deterministic outcome

---

### Pramana's AA Integration Plan

#### Phase 3.5: AA Payload Validation
**New component**: `core/agents/aa_validator.py`

```python
class AAValidatorAgent:
    def check(self, state: PramanaState) -> L1Result:
        """Validates ERC-4337 UserOperation before bundler inclusion."""
        user_op = state.event.aa_user_operation  # new field
        
        # 1. Check callData safety
        # 2. Verify paymaster solvency
        # 3. Validate entrypoint
        # 4. Simulate bundle execution (eth_call)
        
        return L1Result(
            agent_name="aa_validator",
            flagged=unsafe,
            score=risk_from_bundle
        )
```

**Test**:
```bash
pytest tests/test_aa_validator.py -v  # Phase 3.5
```

---

#### Phase 3.6: Bundler Integration
**New route**: `POST /bundler/validate`

```bash
# Bundler calls Pramana before including UserOperation
curl -X POST http://localhost:8000/bundler/validate \
  -H "Content-Type: application/json" \
  -d '{
    "user_operation": {...EntryPoint.UserOperation...},
    "block_number": 1234567,
    "network": "sepolia"
  }'

# Response
{
  "decision": "PASS",  # or PAUSE, ESCALATE
  "risk_score": 0.25,
  "reasoning": "Low risk DeFi swap, within profile thresholds",
  "trace_hash": "0xabc..."
}
```

**Why this matters**:
- Bundler **trusts Pramana's decision** before paying for validation
- If Pramana says PAUSE, bundler doesn't include UserOp
- Prevents malicious agent from wasting bundler's gas

---

#### Phase 3.7: Guardian Integration (Multi-Sig Policy)
**New contract**: `Guardian.sol` deployment to mainnet

```solidity
contract Guardian {
  uint256 public policyVersion;
  bytes32 public policyHash;
  
  function authorizeUserOperation(
    UserOperation calldata userOp,
    bytes calldata pramanaTrace
  ) external onlyCouncil {
    // Council votes (3-judge) on batch of gathered traces
    // If all 3 approve: set policyHash
    // Bundlers only include UserOps that match policyHash
  }
}
```

**Governance loop**:
```
Day 1-7: Gather M traces from agents → DuckDB
Day 8: L2 Council reviews → Vote
Day 9: If approved → Update Guardian.sol policyHash
Day 10+: Bundler checks: "Does this UserOp match latest policy?" → Yes/No
```

---

### Timeline to AA Agents Route

```
[NOW]
  ↓
Step 1: Agent Profiles ✅ DONE
  ↓
Step 2: Financial Recovery (open_positions, recovery playbooks) ⏳ IN PROGRESS
  ├─ Phase 2: LocalNet integration tests
  └─ Phase 2.1-2.5: Single + multi-agent dry runs
  ↓
Step 3: RANSAC Baseline (hallucination detection improvement) ⏳ NEXT
  ├─ Phase 3: Sepolia testnet deployment
  └─ Phase 3.1: Live agent testing (24h)
  ↓
Step 4: Multi-Timescale Drift (behavior tracking) ⏳ AFTER 3
  ├─ Phase 3.5: AA user operation validation
  └─ Enhanced drift detection for AA bundles
  ↓
Step 5: Guardian.sol (multisig policy enforcement) ⏳ AFTER 4
  ├─ Phase 3.6: Bundler integration (POST /bundler/validate)
  └─ Phase 3.7: Guardian.sol deployment + governance loop
  ↓
Step 6: Sybil Guard (stake-gated reputation) ⏳ AFTER 5
  ├─ Phase 4: Mainnet deployment
  └─ AA agents fully operational with stake validation
  ↓
[FINALE: Pramana as AA Agent Safety Layer]
  • OpenClaw-like agents call: POST /bundler/validate
  • Receive: Decision + trace in <500ms
  • Bundler includes UserOp only if PASS
  • On-chain: Guardian enforces latest policy
  • Reputation: SBT tracks agent performance across mainnet
```

---

## Part 5: What to Do RIGHT NOW

### Option A: Verify Current System (5 minutes)
```bash
cd /opt/anaconda3/pramana

# 1. Run smoke tests
python tests/smoke_test.py

# 2. Run unit tests
pytest tests/test_agents.py tests/test_flow.py -v

# Expected: 26/27 passing (1 pre-existing latency issue)
```

**Output**: Confirms Step 1 (Agent Profiles) is solid.

---

### Option B: Start Step 2 (Financial Recovery) - 2 Hours
```bash
# Implement: open_positions recovery logic
# Files to modify:
#   - models.py (already done: open_positions field added)
#   - recovery_playbooks.json (new playbook)
#   - recovery.py (new handler)
#   - flow.py (check positions on pause)

# Then test:
pytest tests/test_recovery.py -v
python tests/smoke_test.py

# Phase 2 becomes unblocked once Step 2 done + tests pass
```

---

### Option C: Build Test Harness for Phase 2 (30 minutes)
```bash
# Create tests/test_phase2_localnet.py
# Template:
#   1. Start 5 agents on Anvil
#   2. Run Pramana for 5 minutes
#   3. Collect metrics (latency, flags, decisions)
#   4. Assert: 100% of decisions received, <500ms avg latency

# This prepares infrastructure for Phase 2.5
```

---

## Summary Table

| Phase | Name | Status | Duration | Blocker |
|-------|------|--------|----------|---------|
| 0 | Smoke tests | ✅ Ready | 30s | None |
| 1 | Unit tests (pytest) | ✅ Ready | 2-3m | None |
| 2 | Anvil integration | ⏳ Blocked | 1h | Step 2 completion |
| 2.1 | Single agent dry-run | ⏳ Blocked | 30m | Step 2 completion |
| 2.5 | Multi-agent LocalNet | ⏳ Blocked | 1h | Step 2 completion |
| 3 | Sepolia testnet | ⏳ Blocked | 24h | Steps 2-3 completion |
| 3.5 | AA validator | ⏳ Blocked | 2h | Step 3-4 completion |
| 3.6 | Bundler integration | ⏳ Blocked | 1h | Step 5 completion |
| 3.7 | Guardian multisig | ⏳ Blocked | 2h | Step 5 completion |
| 4 | Mainnet AA agents | ⏳ Blocked | - | All steps complete |

---

## Recommendation

**🎯 Start with Option B today**: Implement Step 2 (Financial Recovery)

Why:
1. Unblocks Phase 2 immediately
2. Adds critical missing logic (position recovery)
3. Takes only 2 hours
4. Then you can test with actual agents (Phase 2.5)

Ready to proceed?
