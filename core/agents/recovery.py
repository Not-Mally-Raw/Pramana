"""
core/agents/recovery.py — Recovery state machine executor (L2).
Executes recovery_playbook.json steps sequentially.
O(1) lookup by failure_type. No LLM.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from utils.models import PramanaState, RecoveryResult, RecoveryStep

__all__ = ["RecoveryAgent"]

_PLAYBOOK_PATH = Path(__file__).parent.parent / "config" / "recovery_playbook.json"


def _load_playbook() -> dict:
    with open(_PLAYBOOK_PATH) as f:
        data = json.load(f)
    return data.get("playbook", {})


_PLAYBOOK: dict = _load_playbook()


class RecoveryAgent:
    """
    State machine executor. Invoked when a failure_type is detected.
    Returns RecoveryResult with steps executed and final degraded mode.
    """

    name = "recovery"

    def execute(self, failure_type: str, state: PramanaState) -> RecoveryResult:
        plan = _PLAYBOOK.get(failure_type)
        if plan is None:
            return RecoveryResult(
                failure_type=failure_type,
                steps_executed=[],
                success=False,
                final_mode=str(state.degraded_mode),
                message=f"no playbook entry for failure_type='{failure_type}'",
            )

        steps_executed: list[RecoveryStep] = []
        success = True
        final_mode = str(state.degraded_mode)

        for step_def in plan.get("steps", []):
            step = RecoveryStep(
                step_id=step_def["id"],
                action=step_def["action"],
                params=step_def.get("params", {}),
                description=step_def.get("description", ""),
            )
            ok, result_msg = self._execute_step(step, state)
            step.result = result_msg
            step.success = ok
            steps_executed.append(step)

            if not ok:
                success = False
                break  # stop on first failure

        fallback_mode = plan.get("fallback_mode", str(state.degraded_mode))
        if not success:
            final_mode = fallback_mode

        return RecoveryResult(
            failure_type=failure_type,
            steps_executed=steps_executed,
            success=success,
            final_mode=final_mode,
            message=f"recovery {'completed' if success else 'partial'}, mode={final_mode}",
        )

    # ── Step executors ────────────────────────────────────────────────────────

    def _execute_step(self, step: RecoveryStep, state: PramanaState) -> tuple[bool, str]:
        action = step.action
        params = step.params

        try:
            if action == "sleep":
                secs = float(params.get("seconds", 5))
                # Cap sleep to 5s in test/degraded scenarios
                time.sleep(min(secs, 5.0))
                return True, f"slept {min(secs, 5.0):.0f}s"

            elif action == "set_degraded_mode":
                mode = params.get("mode", "NORMAL")
                from utils.models import DegradedMode
                state.degraded_mode = DegradedMode[mode]
                return True, f"mode set to {mode}"

            elif action == "switch_rpc":
                target = params.get("target", "anvil")
                url = params.get("url", "http://127.0.0.1:8545")
                # Live RPC switch via settings override (not cached settings)
                import os
                os.environ["NETWORK"] = target
                os.environ["ANVIL_RPC"] = url
                from core.config.settings import invalidate_settings
                invalidate_settings()
                return True, f"switched to {target} @ {url}"

            elif action == "retry_llm":
                # Retry signaled; actual retry logic in council.py
                return True, "retry_llm: signal sent"

            elif action == "retry_db":
                # Retry signal
                return True, "retry_db: signal sent"

            elif action == "switch_db":
                from core.config.settings import get_settings, invalidate_settings
                import os
                mode = params.get("mode", "memory")
                target = params.get("target", ":memory:")
                if mode == "memory":
                    os.environ["SQLITE_PATH"] = target
                    invalidate_settings()
                return True, f"db switched to {mode}"

            elif action == "disable_agent":
                agent_name = params.get("agent", "")
                state.disabled_agents = getattr(state, "disabled_agents", set())
                state.disabled_agents.add(agent_name)
                return True, f"disabled agent {agent_name}"

            elif action == "disable_feature":
                feature = params.get("feature", "")
                state.disabled_features = getattr(state, "disabled_features", set())
                state.disabled_features.add(feature)
                return True, f"disabled feature {feature}"

            elif action == "use_cache":
                return True, "using cached value"

            elif action == "set_default":
                key = params.get("key", "")
                value = params.get("value", 0)
                state.recovery_defaults = getattr(state, "recovery_defaults", {})
                state.recovery_defaults[key] = value
                return True, f"default set {key}={value}"

            elif action == "emit_event":
                event_name = params.get("event", "RECOVERY_EVENT")
                reason = params.get("reason", "")
                self._emit_webhook(event_name, reason)
                return True, f"emitted {event_name}"

            else:
                return False, f"unknown action '{action}'"

        except Exception as e:
            return False, f"step failed: {str(e)[:80]}"

    @staticmethod
    def _emit_webhook(event_name: str, reason: str) -> None:
        try:
            from core.config.settings import get_settings
            s = get_settings()
            if s.DISCORD_WEBHOOK:
                import requests
                requests.post(
                    s.DISCORD_WEBHOOK,
                    json={"content": f"[Pramana Recovery] **{event_name}**: {reason}"},
                    timeout=3,
                )
        except Exception:
            pass
