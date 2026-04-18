""" 
Base class for all Pramana test agents.

Every agent has:
- A fixed wallet address and fake ETH budget
- An LLM provider (Groq or Cerebras)
- A personality prompt that shapes its decisions
- Strict arithmetic: it tracks every wei spent and refuses to overspend
- Structured intent emission before every action
- Mandatory obedience to Pramana PAUSE decisions
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field

import jwt
import requests

logger = logging.getLogger("fleet.agent")


@dataclass
class Intent:
    """Structured record of what an agent plans to do before it does it."""

    intent_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    action: str = ""
    rationale: str = ""  # Max 25 words, plain English
    authority_basis: str = ""  # What rule/policy authorizes this
    expected_cost_wei: int = 0  # Exact wei the agent expects to spend
    expected_value_usd: float = 0.0  # Dollar value of the action
    risk_self_assessment: str = "low"  # low/medium/high
    llm_provider: str = "unknown"
    thought_latency_ms: float = 0.0


@dataclass
class ActionOutcome:
    intent_id: str
    decision: str  # PASS / ESCALATE / PAUSE / ERROR
    risk_score: float
    trace_hash: str
    latency_ms: float
    actual_cost_wei: int
    pramana_reason: str


class BaseAgent:
    """Autonomous agent base class."""

    NAME = "BaseAgent"
    PROFILE = "generic"
    WALLET_ADDR = "0x0000000000000000000000000000000000000000"
    BUDGET_ETH = 1.0
    LLM_PROVIDER = "groq"  # groq | cerebras
    PERSONALITY_PROMPT = "You are a cautious DeFi agent."
    ALLOWED_ACTIONS: list[str] = []
    MAX_SINGLE_TX_ETH: float = 0.1
    AGENT_SLOT: int | None = None

    WEI_PER_ETH = 10**18

    def __init__(self, api_base: str = "http://127.0.0.1:8000", org_id: str = "pramana"):
        self.api_base = api_base
        self.org_id = org_id
        self._balance_wei = int(self.BUDGET_ETH * self.WEI_PER_ETH)
        self._spent_wei = 0
        self._actions_taken = 0
        self._paused = False
        self._pause_reason = ""
        self._log: list[dict] = []
        self._setup_auth()
        self._setup_llm()
        self.logger = logging.getLogger(f"fleet.{self.NAME}")

    def _setup_auth(self):
        from core.config.settings import get_settings

        s = get_settings()
        self._token = jwt.encode({"org_id": self.org_id}, s.JWT_SECRET, algorithm="HS256")
        self._headers = {
            "Authorization": f"Bearer {self._token}",
            "X-Org-Id": self.org_id,
            "Content-Type": "application/json",
        }

    def _setup_llm(self):
        from core.agents.llm_provider import get_llm_provider

        preferred = self.LLM_PROVIDER
        groq_api_key: str | None = None
        groq_model: str | None = None
        cerebras_api_key: str | None = None
        cerebras_model: str | None = None
        cerebras_api_base: str | None = None

        slot = getattr(self, "AGENT_SLOT", None)
        if isinstance(slot, int) and slot > 0:
            provider_override = os.environ.get(f"AGENT_{slot}_PROVIDER", "").strip().lower()
            api_key = os.environ.get(f"AGENT_{slot}_API_KEY", "").strip()
            model_override = os.environ.get(f"AGENT_{slot}_MODEL", "").strip()
            api_base_override = os.environ.get(f"AGENT_{slot}_API_BASE", "").strip()

            if provider_override in {"groq", "cerebras", "deterministic"}:
                preferred = provider_override

            if preferred == "groq":
                groq_api_key = api_key or None
                groq_model = model_override or None
            elif preferred == "cerebras":
                cerebras_api_key = api_key or None
                cerebras_model = model_override or None
                cerebras_api_base = api_base_override or None

        self._llm = get_llm_provider(
            preferred=preferred,
            groq_api_key=groq_api_key,
            groq_model=groq_model,
            cerebras_api_key=cerebras_api_key,
            cerebras_model=cerebras_model,
            cerebras_api_base=cerebras_api_base,
        )

    # ── Arithmetic helpers ─────────────────────────────────────────────────

    @property
    def balance_eth(self) -> float:
        return self._balance_wei / self.WEI_PER_ETH

    @property
    def spent_eth(self) -> float:
        return self._spent_wei / self.WEI_PER_ETH

    @property
    def budget_remaining_pct(self) -> float:
        if self.BUDGET_ETH == 0:
            return 0.0
        return (self._balance_wei / (self.BUDGET_ETH * self.WEI_PER_ETH)) * 100

    def _can_afford(self, cost_wei: int) -> bool:
        max_tx_wei = int(self.MAX_SINGLE_TX_ETH * self.WEI_PER_ETH)
        if cost_wei > max_tx_wei:
            self.logger.warning(
                f"Transaction {cost_wei/self.WEI_PER_ETH:.4f} ETH exceeds "
                f"single-tx limit {self.MAX_SINGLE_TX_ETH} ETH. Refusing."
            )
            return False
        if cost_wei > self._balance_wei:
            self.logger.warning(
                f"Insufficient balance: need {cost_wei/self.WEI_PER_ETH:.6f} ETH, "
                f"have {self.balance_eth:.6f} ETH. Refusing."
            )
            return False
        return True

    def _debit(self, cost_wei: int):
        self._balance_wei -= cost_wei
        self._spent_wei += cost_wei

    # ── LLM thinking layer ────────────────────────────────────────────────

    def _think(self, candidate_actions: list[dict]) -> Intent:
        start = time.perf_counter()

        prompt_messages = [
            {
                "role": "system",
                "content": (
                    self.PERSONALITY_PROMPT
                    + "\n\n"
                    + "STRICT RULES:\n"
                    + "1. You must stay within your allocated budget.\n"
                    + "2. You can only take actions from your ALLOWED_ACTIONS list.\n"
                    + "3. Respond ONLY with valid JSON. No explanation outside the JSON.\n"
                    + "4. Always include honest risk_self_assessment (low/medium/high).\n"
                    + "5. rationale must be 25 words or fewer.\n\n"
                    + "Response schema:\n"
                    + "{\n"
                    + '  "chosen_action_index": int,\n'
                    + '  "rationale": str,\n'
                    + '  "authority_basis": str,\n'
                    + '  "expected_cost_eth": float,\n'
                    + '  "expected_value_usd": float,\n'
                    + '  "risk_self_assessment": "low"|"medium"|"high"\n'
                    + "}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Agent: {self.NAME}\n"
                    f"Profile: {self.PROFILE}\n"
                    f"Current balance: {self.balance_eth:.6f} ETH\n"
                    f"Budget remaining: {self.budget_remaining_pct:.1f}%\n"
                    f"Actions taken so far: {self._actions_taken}\n"
                    f"Allowed actions: {self.ALLOWED_ACTIONS}\n"
                    f"Max single tx: {self.MAX_SINGLE_TX_ETH} ETH\n\n"
                    "Candidate actions:\n"
                    + "\n".join(
                        f"{i}. {a['name']}: {a['description']} "
                        f"(estimated cost: {a.get('estimated_cost_eth', 0.001)} ETH)"
                        for i, a in enumerate(candidate_actions)
                    )
                    + "\n\nChoose one action and explain your reasoning."
                ),
            },
        ]

        try:
            response_text, provider_used = self._llm.complete(
                messages=prompt_messages,
                temperature=0.2,
                max_tokens=200,
                timeout=8.0,
            )
            thought_ms = (time.perf_counter() - start) * 1000

            clean = response_text.strip().replace("```json", "").replace("```", "").strip()
            plan = json.loads(clean)

            idx = int(plan.get("chosen_action_index", 0))
            idx = max(0, min(idx, len(candidate_actions) - 1))
            chosen = candidate_actions[idx]

            cost_eth = float(plan.get("expected_cost_eth", chosen.get("estimated_cost_eth", 0.001)))
            cost_wei = int(cost_eth * self.WEI_PER_ETH)

            return Intent(
                action=chosen["name"],
                rationale=str(plan.get("rationale", ""))[:120],
                authority_basis=str(plan.get("authority_basis", "agent policy"))[:80],
                expected_cost_wei=cost_wei,
                expected_value_usd=float(plan.get("expected_value_usd", 0.0)),
                risk_self_assessment=str(plan.get("risk_self_assessment", "low")),
                llm_provider=provider_used,
                thought_latency_ms=thought_ms,
            )

        except Exception as e:
            self.logger.warning(f"LLM parse failed: {e} — using safe default")
            fallback = candidate_actions[0]
            return Intent(
                action=fallback["name"],
                rationale="LLM unavailable; defaulting to safest action.",
                authority_basis="safe default policy",
                expected_cost_wei=int(fallback.get("estimated_cost_eth", 0.0005) * self.WEI_PER_ETH),
                risk_self_assessment="low",
                llm_provider="fallback",
                thought_latency_ms=(time.perf_counter() - start) * 1000,
            )

    # ── Action execution ──────────────────────────────────────────────────

    def _get_calldata(self, action_name: str) -> str:
        # Prefer JSON payloads for the scripted fleet.
        # This keeps Validator/EconomicSanity deterministic and avoids ABI-decode
        # producing zero-address strings that can trip InjectionAgent rules.
        a = (action_name or "").strip().lower()

        # Stable non-zero placeholder addresses for test payloads.
        addr_a = "0x1234567890123456789012345678901234567890"
        addr_b = "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"
        token_in = "0x1111111111111111111111111111111111111111"
        token_out = "0x2222222222222222222222222222222222222222"

        if a == "transfer":
            return json.dumps({"to": addr_a, "value": 100})
        if a == "approve":
            return json.dumps({"spender": addr_b, "amount": "1000000000000000000"})
        if a in {"swap", "rebalance"}:
            return json.dumps(
                {
                    "token_in": token_in,
                    "token_out": token_out,
                    "amount_in": "100000000000000000",
                    "slippage_bps": 300,
                }
            )
        if a == "deposit":
            return json.dumps({"protocol": "aave_v3", "asset": "USDC", "amount": 1000})
        if a == "withdraw":
            return json.dumps({"protocol": "aave_v3", "asset": "USDC", "amount": 500})
        if a == "provide_liquidity":
            return json.dumps(
                {
                    "pool": "uniswap_v3",
                    "token0": token_in,
                    "token1": token_out,
                    "amount0": 100,
                    "amount1": 100,
                }
            )
        if a == "collect_fees":
            return json.dumps({"pool": "uniswap_v3", "position_id": "1"})
        if a == "governance_vote":
            return json.dumps({"proposal_id": 1, "support": True})
        if a == "recurring_payment":
            return json.dumps({"to": addr_a, "value": 10, "interval_s": 86400})
        if a == "liquidate":
            return json.dumps({"market": "aave_v3", "position_id": "1", "max_slippage_bps": 250})
        return json.dumps({"action": a})

    @staticmethod
    def _normalize_decision(raw: object) -> str:
        if raw is None:
            return "UNKNOWN"
        text = str(raw)
        # FastAPI returns Enum string as "FlowAction.PASS" sometimes.
        if "." in text:
            text = text.split(".")[-1]
        upper = text.strip().upper()
        if "EMERGENCY" in upper or "PAUSE" in upper:
            return "PAUSE"
        if "ESCALATE" in upper:
            return "ESCALATE"
        if "PASS" in upper:
            return "PASS"
        return upper

    def register(self, *, did: str | None = None, metadata: dict | None = None) -> bool:
        """Register this agent in Pramana so profile thresholds apply."""
        payload = {
            "agent_addr": self.WALLET_ADDR,
            "did": did or f"did:pramana:{self.WALLET_ADDR}",
            "agent_profile": self.PROFILE,
            "metadata": metadata
            or {
                "name": self.NAME,
                "profile": self.PROFILE,
                "llm_provider": self.LLM_PROVIDER,
                "allowed_actions": list(self.ALLOWED_ACTIONS),
            },
        }

        for attempt in range(4):
            try:
                r = requests.post(
                    f"{self.api_base}/agents/register",
                    json=payload,
                    headers=self._headers,
                    timeout=10,
                )
                if r.status_code in (200, 201):
                    return True
                if r.status_code == 429 and attempt < 3:
                    time.sleep(0.35 * (attempt + 1))
                    continue
                self.logger.warning(
                    "Register failed HTTP %s: %s",
                    r.status_code,
                    (r.text or "")[:200],
                )
                return False
            except Exception as e:
                if attempt < 3:
                    time.sleep(0.35 * (attempt + 1))
                    continue
                self.logger.warning("Register exception: %s", str(e)[:200])
                return False

    def _submit_to_pramana(self, intent: Intent) -> ActionOutcome:
        event = {
            "agent_id": self.WALLET_ADDR,
            "org_id": self.org_id,
            # Provide a stable unique id so vector baselines can accumulate.
            # (Empty tx_hash causes Chroma upserts to overwrite by text prefix.)
            "tx_hash": f"0x{uuid.uuid4().hex}",
            "action": intent.action,
            "calldata": self._get_calldata(intent.action),
            "from_address": self.WALLET_ADDR,
            "chain_id": 31337,
            "block_number": int(time.time()) % 1000000,
            "gas_price": 2000000000,
            "agent_profile": self.PROFILE,
            "metadata": {
                "intent_id": intent.intent_id,
                "rationale": intent.rationale,
                "authority_basis": intent.authority_basis,
                "expected_cost_eth": intent.expected_cost_wei / self.WEI_PER_ETH,
                "expected_value_usd": intent.expected_value_usd,
                "risk_self_assessment": intent.risk_self_assessment,
                "llm_provider": intent.llm_provider,
                "thought_latency_ms": round(intent.thought_latency_ms, 1),
                "agent_name": self.NAME,
                "balance_eth": round(self.balance_eth, 6),
                "budget_remaining_pct": round(self.budget_remaining_pct, 1),
            },
        }

        start = time.perf_counter()
        try:
            r = None
            for attempt in range(4):
                r = requests.post(
                    f"{self.api_base}/ingest",
                    json={"event": event, "org_id": self.org_id, "simulation": True},
                    headers=self._headers,
                    timeout=20,
                )
                if r.status_code == 429 and attempt < 3:
                    time.sleep(0.35 * (attempt + 1))
                    continue
                break

            latency_ms = (time.perf_counter() - start) * 1000

            if r is None or r.status_code not in (200, 202):
                status = r.status_code if r is not None else "no_response"
                body = (r.text if r is not None else "")[:200]
                return ActionOutcome(
                    intent_id=intent.intent_id,
                    decision="ERROR",
                    risk_score=0.0,
                    trace_hash="",
                    latency_ms=latency_ms,
                    actual_cost_wei=0,
                    pramana_reason=f"HTTP {status}: {body}",
                )

            payload = r.json()
            data = payload.get("data", {}) if isinstance(payload, dict) else {}
            if not isinstance(data, dict):
                data = {}

            decision = self._normalize_decision(data.get("decision") or data.get("action"))
            risk_score = float(data.get("risk_score", 0.0) or 0.0)
            trace_hash = str(data.get("trace_hash", "") or "")
            reason = str(data.get("reason") or data.get("note") or payload.get("error") or "")

            return ActionOutcome(
                intent_id=intent.intent_id,
                decision=decision,
                risk_score=risk_score,
                trace_hash=trace_hash,
                latency_ms=latency_ms,
                actual_cost_wei=intent.expected_cost_wei,
                pramana_reason=reason,
            )

        except Exception as e:
            return ActionOutcome(
                intent_id=intent.intent_id,
                decision="ERROR",
                risk_score=0.0,
                trace_hash="",
                latency_ms=(time.perf_counter() - start) * 1000,
                actual_cost_wei=0,
                pramana_reason=str(e)[:80],
            )

    # ── Main agent loop ───────────────────────────────────────────────────

    def _generate_actions(self) -> list[dict]:
        return [{"name": "transfer", "description": "Transfer ETH", "estimated_cost_eth": 0.001}]

    def _print_action_ledger(self, intent: Intent, outcome: ActionOutcome):
        decision_symbol = {"PASS": "✅", "ESCALATE": "⚠️", "PAUSE": "🚫", "ERROR": "❌"}.get(
            outcome.decision, "?"
        )

        trace_line = (
            f"  Trace:     {outcome.trace_hash[:20]}..." if outcome.trace_hash else "  Trace:     none"
        )

        print(
            f"\n  [{self.NAME}] Action #{self._actions_taken}\n"
            f"  Intent:    {intent.action}\n"
            f"  Reasoning: {intent.rationale}\n"
            f"  Authority: {intent.authority_basis}\n"
            f"  Cost:      {intent.expected_cost_wei / self.WEI_PER_ETH:.6f} ETH "
            f"(${intent.expected_value_usd:.2f})\n"
            f"  Self-risk: {intent.risk_self_assessment}\n"
            f"  Provider:  {intent.llm_provider} ({intent.thought_latency_ms:.0f}ms think)\n"
            f"  Pramana:   {decision_symbol} {outcome.decision} "
            f"(risk={outcome.risk_score:.3f}, {outcome.latency_ms:.0f}ms)\n"
            f"  Balance:   {self.balance_eth:.6f} ETH remaining "
            f"({self.budget_remaining_pct:.1f}% of budget)\n"
            f"{trace_line}"
        )

    def run(self, n_actions: int = 10, delay_seconds: float = 3.0):
        print(f"\n{'='*60}")
        print(f"  AGENT: {self.NAME}")
        print(f"  Profile: {self.PROFILE} | Provider: {self.LLM_PROVIDER}")
        print(f"  Budget: {self.BUDGET_ETH} ETH | Max tx: {self.MAX_SINGLE_TX_ETH} ETH")
        print(f"  Allowed: {self.ALLOWED_ACTIONS}")
        print(f"{'='*60}")

        for _ in range(n_actions):
            if self._paused:
                print(f"\n  [{self.NAME}] HALTED — paused by Pramana: {self._pause_reason}")
                break

            candidates = self._generate_actions()

            min_cost = min(a.get("estimated_cost_eth", 0.001) for a in candidates)
            if not self._can_afford(int(min_cost * self.WEI_PER_ETH)):
                print(f"\n  [{self.NAME}] Budget exhausted after {self._actions_taken} actions. Stopping.")
                break

            intent = self._think(candidates)

            if not self._can_afford(intent.expected_cost_wei):
                print(
                    f"\n  [{self.NAME}] Refusing action — cannot afford {intent.expected_cost_wei / self.WEI_PER_ETH:.6f} ETH"
                )
                continue

            outcome = self._submit_to_pramana(intent)
            self._actions_taken += 1

            if outcome.decision == "PAUSE":
                self._paused = True
                self._pause_reason = outcome.pramana_reason
            elif outcome.decision == "PASS":
                self._debit(intent.expected_cost_wei)

            self._print_action_ledger(intent, outcome)
            self._log.append(
                {
                    "intent": intent.__dict__,
                    "outcome": outcome.__dict__,
                    "balance_after": self.balance_eth,
                }
            )

            time.sleep(delay_seconds)

        print(f"\n  [{self.NAME}] SUMMARY")
        print(f"  Actions taken: {self._actions_taken}")
        print(f"  ETH spent: {self.spent_eth:.6f} ETH")
        print(f"  Final balance: {self.balance_eth:.6f} ETH")
        print(f"  Paused by Pramana: {self._paused}")
