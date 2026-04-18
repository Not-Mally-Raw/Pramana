# Pramana – AI-Powered Agent Validation & Recovery for ERC-4337

> **State-of-the-art blockchain agent governance with production-grade reliability**

Pramana is an intelligent system that protects blockchain operations from attacks using a hierarchical council of AI agents. It combines **CrewAI**, **Groq LLM**, **ERC-4337 bundlers**, and **on-chain attestations** to create a production-ready agent security layer.

## ✨ Key Features

- **🤖 AI Agent Council**: 7 specialized agents analyzing transactions for attacks
- **⚡ Sub-100ms Response**: Real-time decision making for blockchain operations
- **🔒 Attack Detection**: Injection, economic, network, and behavioral anomalies
- **📊 Real-time Dashboard**: Monitor agents and operations in real-time
- **🔗 On-Chain Records**: Verifiable attestations for compliance
- **💰 Financial Recovery**: Automatic remediation for escalated accounts
- **🧪 Production Tested**: Full test suite with 41 passing tests
- **📱 User-Friendly**: Modern Next.js interface with zero-friction onboarding

## 🚀 Quick Start

### 1. Prerequisites
```bash
# Required: Python 3.12+, Node.js 18+, Foundry
python3 --version
node --version
curl -L https://foundry.paradigm.xyz | bash && foundryup
```

### 2. Setup (2 minutes)
```bash
# Clone and install
git clone <repo>
cd pramana
cp .env.example .env

# Edit .env: Add GROQ_API_KEY (free at console.groq.com)
nano .env

# Secrets auto-generated on first start:
# - JWT_SECRET
# - ATTESTATION_HMAC_SECRET
```

### 3. Start Everything (1 command)
```bash
# Development mode with hot-reload
./scripts/start_prod.sh --dev

# Or production-optimized
./scripts/start_prod.sh
```

### 4. Access
- **Dashboard**: http://localhost:3000
- **API Docs**: http://localhost:8000/docs
- **API Status**: http://localhost:8000/health

**That's it!** Pramana is now protecting operations.

## 📖 Complete Documentation

- **[QUICK_START.md](QUICK_START.md)** ← Start here for user-friendly guide
- **[PRODUCTION_SETUP.md](PRODUCTION_SETUP.md)** ← Comprehensive deployment guide
- **[API Docs](http://localhost:8000/docs)** ← Interactive API reference (when running)

## 🏗️ Architecture

```
User Request
    ↓
┌─────────────────────────────────────┐
│  FastAPI Backend (port 8000)        │
│  ┌────────────────────────────────┐ │
│  │ L1 Agent Council (7 agents)    │ │
│  │ • InjectionAgent               │ │
│  │ • EconomicSanityAgent          │ │
│  │ • NetworkAgent                 │ │
│  │ • OperationsAgent              │ │
│  │ • ValidatorAgent               │ │
│  │ • HallucinationAgent           │ │
│  │ • DriftMonitorAgent            │ │
│  └────────────────────────────────┘ │
│  ↓ (if escalated)                   │
│  ┌────────────────────────────────┐ │
│  │ L2 Council Voting (3 agents)   │ │
│  │ • CouncilL2Agent               │ │
│  │ • RecoveryAgent                │ │
│  │ • ReputationAgent              │ │
│  └────────────────────────────────┘ │
│  ↓ (final decision)                 │
│  PASS / ESCALATE / PAUSE            │
└─────────────────────────────────────┘
    ↓
Next.js Dashboard (port 3000)
    ↓
On-Chain Attestation (via Orchestrator)
```

## 🎯 How It Works

### 1. Submission
Operations are submitted via API or dashboard for analysis.

### 2. L1 Analysis (Parallel)
7 agents run simultaneously analyzing the operation:
- **InjectionAgent**: Detects prompt injection attacks (<20ms)
- **EconomicSanityAgent**: Checks for financial anomalies (<50ms)
- **NetworkAgent**: Monitors gas and RPC patterns (<30ms)
- **OperationsAgent**: Detects action loops and balance drains (<40ms)
- **ValidatorAgent**: Validates structure and addresses (<20ms)
- **HallucinationAgent**: Checks for off-policy behavior (<30ms)
- **DriftMonitorAgent**: Detects behavioral drift (<40ms)

### 3. Risk Fusion
Scores from all agents are fused using weighted averaging:
- Risk Score = avg(L1_scores) + flag_density_bonus - uncertainty_penalty
- Thresholds:
  - **PASS** (<0.40): Safe to execute
  - **ESCALATE** (0.40-0.70): Send to council
  - **PAUSE** (>0.70): Block and alert

### 4. L2 Council (if escalated)
For risky operations, specialist council votes:
- **CouncilL2Agent**: Reviews escalated cases
- **RecoveryAgent**: Plans remediation
- **ReputationAgent**: Updates agent reputation scores

### 5. On-Chain Recording
Decision is recorded via on-chain attestation (SBT).

## 📊 Dashboard Features

### Overview
- Real-time metrics (operations/min, escalation rate, etc.)
- System health status
- Active agents and decision distribution

### Agents
- View all agent configurations
- Monitor performance metrics
- Adjust weights and thresholds

### Evaluations
- Detailed analysis of each operation
- Agent-by-agent risk breakdown
- Decision traces and reasoning

### Lifecycle
- Track operations from submission to finalization
- View attestation records on-chain
- Monitor recovery attempts

## 🧪 Testing

### Unit Tests (all passing)
```bash
python -m pytest tests/ -v
# Result: 41 passed ✅
```

### Behavioral Simulation
```bash
# Simulate 10 agents making transactions
PYTHONPATH=. python scripts/behavior_simulator.py \
  --agents 10 \
  --duration 60 \
  --anvil-duration 30

# Test with injection attack
PYTHONPATH=. python scripts/testnet_emitter.py \
  --org pramana \
  --userop-json '{"callData": "0x12345ignore_previous_instructions"}'
```

### Live Fleet Demo With Per-Agent Keys
```bash
# 1) Create agent key file (5 slots: 1-3 Groq, 4-5 Cerebras)
cp scripts/agents/.env.agents.example scripts/agents/.env.agents

# 2) Paste your keys/models into scripts/agents/.env.agents

# 3) Run live fleet traffic for dashboard
PYTHONPATH=. python scripts/agents/fleet_runner.py \
  --api-url http://127.0.0.1:8000 \
  --org demo-prof \
  --mode sequential \
  --actions 4 \
  --delay 0.45 \
  --summary-json outputs/live_ui_demo/fleet_live.json
```

For dashboard org targeting, use `frontend/.env.local.example` as template and set
`NEXT_PUBLIC_PRAMANA_ORG_ID` to the same org passed to `fleet_runner.py`.

### Detection Baseline Evaluation
```bash
# Reproducible precision/recall/FPR artifact with baseline comparisons
PYTHONPATH=. python scripts/evaluate_detection_baselines.py \
  --samples-per-class 100 \
  --seed 42 \
  --output outputs/detection_eval_100.json
```
This writes a JSON artifact with confusion matrices and attack-type breakdowns.

### Full Validation Gate (Recommended)
```bash
# One command: API contracts + pytest + baseline eval + fleet eval + bundler probe
PYTHONPATH=. python scripts/validation/run_validation_gate.py \
  --api-url http://127.0.0.1:8000 \
  --org test_pilot
```
This writes a consolidated report to `outputs/validation_gate/validation_report.json`.

### Full Sprint Simulation
```bash
# Comprehensive: register agents → load test → attack simulation → metrics
PYTHONPATH=. python scripts/simulate_sprint.py
```

## 🔐 Security

- **JWT Authentication**: HS256 tokens for API access
- **HMAC Attestations**: Cryptographic proof of decisions
- **On-Chain Verification**: Decisions recorded in smart contracts
- **Rate Limiting**: Prevents API abuse (built-in Uvicorn)
- **Secrets Management**: No hardcoded keys, environment-based

## 🌍 Deployment Options

### Development
```bash
./scripts/start_prod.sh --dev
```
- Hot reload for Python and frontend
- Debug logs enabled
- Perfect for local development

### Production
```bash
./scripts/start_prod.sh --network sepolia
```
- Optimized startup
- Minimal logging
- Production network support:
  - **anvil**: Local testnet (development)
  - **sepolia**: Ethereum testnet
  - **base**: Base mainnet
  - (more networks coming)

### Docker
```bash
docker build -f Dockerfile.prod -t pramana:latest .
docker run -p 8000:8000 \
  -e GROQ_API_KEY=... \
  -e JWT_SECRET=$(openssl rand -hex 32) \
  pramana:latest
```

## 📊 System Status

| Component | Status | Health |
|-----------|--------|--------|
| Backend API | ✅ Running | 200 OK |
| Frontend | ✅ Running | Loading |
| Anvil Node | ✅ Running | Connected |
| Databases | ✅ Ready | Initialized |
| Agent Council | ✅ Active | 7/7 agents |
| Tests | ✅ All Pass | 41/41 ✓ |

## 🛠️ Configuration

### Environment Variables

**Required:**
- `GROQ_API_KEY` - Get free at [console.groq.com](https://console.groq.com)
- `JWT_SECRET` - Generate: `openssl rand -hex 32`
- `ATTESTATION_HMAC_SECRET` - Generate: `openssl rand -hex 32`

**Optional (auto-configured):**
- `NETWORK` - anvil (default), sepolia, or base
- `LOG_LEVEL` - DEBUG, INFO, WARNING, ERROR
- `DEBUG` - true/false

**Auto-generated on first run:**
- Secrets if not provided
- SQLite and DuckDB databases
- Chroma vector store

See [.env.example](.env.example) for all options.

## 🚨 Troubleshooting

### Port Already in Use
```bash
kill $(lsof -t -i:8000)  # Free port 8000
kill $(lsof -t -i:3000)  # Free port 3000
```

### Missing Anvil
```bash
curl -L https://foundry.paradigm.xyz | bash
source ~/.bashrc
foundryup
```

### GROQ API Key Issues
Get a free key at https://console.groq.com and add to `.env`:
```
GROQ_API_KEY=gsk_your_key_here
```

### Database Locked
```bash
pkill -f uvicorn
pkill -f anvil
sleep 2
./scripts/start_prod.sh --dev
```

See [PRODUCTION_SETUP.md](PRODUCTION_SETUP.md#troubleshooting) for more solutions.

## 📚 Learning Resources

### Understanding Pramana
1. Read [QUICK_START.md](QUICK_START.md) for user guide
2. Explore [PRODUCTION_SETUP.md](PRODUCTION_SETUP.md) for technical details
3. Check [API Docs](http://localhost:8000/docs) for endpoint reference
4. Review test cases in [tests/](tests/) for usage examples

### Key Concepts
- **UserOperation**: ERC-4337 abstraction accounts
- **Bundler**: Aggregates UserOps and submits batches
- **Attestation**: On-chain proof of analysis decision
- **Risk Fusion**: How multi-agent scores combine into decisions
- **Recovery**: Automatic remediation for flagged operations

## 🎓 Examples

### API Call
```bash
# Get metrics
TOKEN=$(python3 -c "import jwt; from core.config.settings import get_settings; s = get_settings(); print(jwt.encode({'org_id': 'pramana'}, s.JWT_SECRET, algorithm='HS256'))")

curl -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8000/metrics/realtime | python -m json.tool
```

### Dashboard Navigation
1. Open http://localhost:3000
2. Open "Launchpad" to run readiness checks and 5-key provider validation
3. Open "Command Center" for live active-agent and decision feed
4. Open "Flow Explorer" to inspect DAG and latency views
5. Open "Agents" and "Evaluations" for deep-dive analysis

### Register Test Agents
```bash
PYTHONPATH=. python scripts/register_agents.py --org pramana
```

## 🤝 Contributing

Found an issue? Have a feature request?

1. Check existing [issues](issues/)
2. Create detailed bug report with:
   - System info (OS, Python version, Node version)
   - Steps to reproduce
   - Expected vs actual behavior
   - Log output from `.logs/`

## 📄 License

MIT License - See LICENSE file

## 🙋 Support

- **Documentation**: [PRODUCTION_SETUP.md](PRODUCTION_SETUP.md)
- **Quick Help**: [QUICK_START.md](QUICK_START.md)
- **API Reference**: http://localhost:8000/docs (when running)
- **Issues**: Create an issue on GitHub

---

## 🎉 Ready to Go?

1. **Quick Start**: Run `./scripts/start_prod.sh --dev` 
2. **Explore**: Open http://localhost:3000
3. **Test**: Run `PYTHONPATH=. python scripts/simulate_sprint.py`
4. **Learn**: Read [QUICK_START.md](QUICK_START.md)

**Pramana is now protecting your operations with AI-powered analysis!** 🚀
