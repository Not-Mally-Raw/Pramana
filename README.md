# Pramãna – On-Chain AgentOps Platform

> **Governing the Unseen: Traceability for On-Chain AI Swarms**

Pramãna is a full hierarchical multi-agent system for monitoring, governing, and auditing AI agents operating on blockchain networks. It combines **CrewAI Flows**, **Groq LLM**, **Solidity smart contracts**, and **ElizaOS** into a production-grade AgentOps platform.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    LEVEL 0 — PramanaFlow                        │
│              (CrewAI Flow[PramanaState] orchestrator)           │
└────────────────────────────┬────────────────────────────────────┘
                             │ ChainEvent
              ┌──────────────▼──────────────┐
              │     LEVEL 1 — Monitoring Crew│
              │  Network → Ops → Halluc →    │  Deterministic only
              │  Inject → Validator → Drift  │  2-pass verification
              └──────────────┬──────────────┘
                             │ L1Results
              ┌──────────────▼──────────────┐
              │   Escalation Router (O(1))   │  Dict lookup, no LLM
              └──┬──────────┬──────────┬───┘
                 │PASS      │ESCALATE  │PAUSE
              ┌──▼──┐   ┌──▼────────────▼──┐
              │ Log │   │LEVEL 2 — Council  │  3× sequential Groq
              └─────┘   │Council  + Recovery│  Llama-3.3-70B
                        │+ Reputation↗SBT   │  Weighted majority
                        └────────────────────┘
                                 │
              ┌──────────────────▼──────────────────────┐
              │          TraceDAG Finalization           │
              │  SHA-256 hash → TraceFinalized on-chain  │
              └──────────────────────────────────────────┘
```

### L1 Agents (7 — deterministic, <100ms each)

| Agent | What it checks | Pass 1 | Pass 2 |
|---|---|---|---|
| **NetworkAgent** | Gas spike >2× avg, RPC health | DuckDB rolling avg | Anvil re-ping |
| **OperationsAgent** | Action loops, balance drain >10% | SQLite window count | Tight 30s recount |
| **HallucinationAgent** | Off-policy outputs | Chroma cosine similarity | DuckDB success rate |
| **InjectionAgent** | OWASP LLM Top 10 v1.1 | Compiled regex (24 patterns) | ABI selector check |
| **ValidatorAgent** | JSON schema, address format | Schema validation | `eth_call` dry-run |
| **DriftMonitorAgent** | Behavioral drift from baseline | Chroma cosine vs baseline | Rolling 10-tx window |

### L2 Agents (3)

| Agent | Role |
|---|---|
| **CouncilAgent** | 3× sequential Groq calls (Llama-3.3-70B, temps 0/0.5/1.0, weighted majority) |
| **RecoveryAgent** | O(1) state machine from `recovery_playbook.json`, no LLM |
| **ReputationAgent** | DuckDB aggregation → composite score → `batchUpdateScores()` on SBT |

### Smart Contracts (Solidity 0.8.24)

| Contract | Purpose |
|---|---|
| **Orchestrator.sol** | Agent registry, `TraceFinalized` event (~3K gas), council decisions |
| **Guardian.sol** | Versioned policy hashes, rollback, audit trail |
| **ReputationSBT.sol** | Non-transferable ERC-721, batch score updates, composite formula |

---

## Quick Start

### Prerequisites

- Python 3.11+
- [Foundry](https://foundry.paradigm.xyz/) (for contract deployment)
- [Anvil](https://book.getfoundry.sh/anvil/) (local chain, included with Foundry)
- Node.js 20+ (for TypeScript SDK only)

### 1. Install dependencies

```bash
cd /path/to/pramana
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env: add GROQ_API_KEY (free at console.groq.com)
```

### 3. Initialize

```bash
python pramana_cli.py init
```

### 4. Start Anvil (local blockchain)

```bash
anvil
# Leaves running in another terminal
```

### 5. Deploy contracts

```bash
python pramana_cli.py deploy
# Updates ORCHESTRATOR_ADDR, GUARDIAN_ADDR, SBT_ADDR in .env
```

### 6. Start API server

```bash
python pramana_cli.py run
# API: http://localhost:8000
# Docs: http://localhost:8000/docs
```

### 7. Launch dashboard

```bash
python pramana_cli.py dashboard
# http://localhost:8501
```

### 8. Run benchmark

```bash
python pramana_cli.py bench --n 100
```

---

## Directory Structure

```
pramana/
├── contracts/                   # Solidity 0.8.24
│   ├── Orchestrator.sol         # Agent registry + TraceFinalized
│   ├── Guardian.sol             # Policy hash store
│   ├── ReputationSBT.sol        # Soulbound token
│   └── script/Deploy.s.sol      # Forge deploy script
├── core/
│   ├── app.py                   # FastAPI service
│   ├── flow.py                  # PramanaFlow (CrewAI Flow[PramanaState])
│   ├── db.py                    # SQLite + DuckDB
│   ├── scheduler.py             # APScheduler jobs
│   ├── listener.py              # WebSocket listener
│   ├── agents/
│   │   ├── base.py              # BaseL1Agent (two-pass template)
│   │   ├── network.py           # Gas / RPC health
│   │   ├── operations.py        # Loop / drain detection
│   │   ├── hallucination.py     # Chroma cosine
│   │   ├── injection.py         # OWASP LLM Top 10 regex
│   │   ├── validator.py         # Schema + eth_call sim
│   │   ├── drift.py             # Behavioral drift
│   │   ├── council.py           # 3× Groq weighted vote
│   │   ├── recovery.py          # Playbook state machine
│   │   └── reputation.py        # DuckDB → SBT
│   ├── crews/
│   │   ├── monitoring.py        # L1 Crew
│   │   └── intervention.py      # L2 Crew
│   └── config/
│       ├── settings.py          # Pydantic Settings
│       ├── routing_rules.json   # Declarative escalation rules
│       └── recovery_playbook.json
├── sdk/                         # TypeScript @pramana/eliza-plugin
│   └── src/
│       ├── plugin.ts            # ElizaOS plugin entry
│       ├── trace.ts             # @trace decorator
│       ├── guard.ts             # @guard decorator
│       └── types.ts             # TypeScript interfaces
├── dashboard/
│   ├── app.py                   # Streamlit main
│   └── pages/
│       ├── overview.py          # Live agent table + charts
│       ├── policies.py          # NL policy editor
│       ├── replay.py            # TraceDAG viewer
│       └── identity.py          # SBT scores + history
├── utils/
│   ├── models.py                # All Pydantic models
│   ├── graph.py                 # TraceDAG (NetworkX)
│   ├── cache.py                 # TTL/LRU caches + Groq RPM limiter
│   └── crypto.py                # SHA-256, keccak256, Merkle
├── tests/
│   ├── test_agents.py
│   ├── test_flow.py
│   └── benchmark_pipeline.py
├── pramana_cli.py               # Click CLI
├── requirements.txt
└── .env.example
```

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Basic health check |
| `GET` | `/health/detailed` | SQLite + node health |
| `POST` | `/agents/register` | Register an agent |
| `GET` | `/agents` | List org agents |
| `GET` | `/agents/{addr}` | Get agent details |
| `GET` | `/traces` | List recent traces |
| `GET` | `/replay/{flow_id}` | Full TraceDAG replay |
| `POST` | `/policies` | Create guard policy |
| `GET` | `/policies/{addr}` | List agent policies |
| `POST` | `/config/routing` | Hot-reload routing rules |
| `POST` | `/guard/check` | Pre-action guard check (SDK) |
| `POST` | `/ingest` | Manually inject event (testing) |

All endpoints accept `X-Org-Id` header for multi-tenancy.

---

## ElizaOS Plugin Usage

```typescript
import { createPramanaPlugin } from "@pramana/eliza-plugin";

// In your ElizaOS character file:
export const character = {
  name: "MyAgent",
  plugins: [
    createPramanaPlugin({
      apiUrl: "http://localhost:8000",
      orgId: "my-org",
      guardEnabled: true,   // block injection attempts
      traceEnabled: true,   // emit traces to Pramana
    }),
  ],
};

// Or use decorators directly:
class MyAgent {
  @guard(config)
  @trace(config)
  async executeSwap(params: SwapParams) {
    // Guard runs BEFORE, trace captures start/end
  }
}
```

---

## Design Decisions

### Groq Free Tier Compliance
- **25 RPM hard cap** enforced in `utils/cache.py`
- L1 agents: **0 Groq calls** (deterministic only)
- Council: **3 sequential** calls with 2s delays (not parallel)
- L1 uses Mixtral (fast), Council uses Llama-3.3-70B (quality)

### Symphony-Derived Patterns (adopted)
| Pattern | Source | Where used |
|---|---|---|
| Declarative routing table | Symphony | `routing_rules.json` + O(1) dict lookup |
| Interaction DAG as trace | Symphony | `utils/graph.py` TraceDAG |
| Two-pass verification | Symphony | `BaseL1Agent.run()` template |

### Storage Budget (<2GB RSS target)
- **SQLite**: state/events, LRU evict to 10K rows/org
- **DuckDB**: analytics queries only
- **Chroma HNSW**: drift embeddings, ~200MB

### Security
- All Pydantic models validate inputs at API boundary
- `InjectionAgent` checks OWASP LLM Top 10 v1.1 patterns before any LLM call
- `eth_call` simulation on Anvil before any transaction broadcast
- Private keys only in `.env`, never in source

---

## Benchmarks

On Apple M1 (8GB RAM), Anvil local chain:

| Metric | Value |
|---|---|
| L1 pipeline p50 latency | ~45ms |
| L1 pipeline p95 latency | ~120ms |
| Throughput | ~20 events/sec |
| Groq Council call (3 judges) | ~8–12s |
| TraceDAG finalize (SHA-256) | <1ms |
| `TraceFinalized` on-chain gas | ~3,000 gas |

---

## Testing

```bash
# Unit tests
pytest tests/test_agents.py tests/test_flow.py -v

# Benchmark
python tests/benchmark_pipeline.py --n 100

# With coverage
pytest tests/ --cov=core --cov=utils --cov-report=term-missing
```

---

## License

MIT — see [LICENSE](LICENSE)
