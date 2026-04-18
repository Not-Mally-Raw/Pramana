"""
core/agents/llm_provider.py — Groq + Cerebras abstraction.

This module provides a small, dependency-light interface for agent-facing LLM calls.
It is intentionally minimal so it can be used by CLI scripts and local tooling.

API:
- get_llm_provider(preferred: str | None = None) -> LLMProvider
- LLMProvider.complete(...) -> (text, provider_name)

Providers:
- Groq: uses the `groq` Python package (OpenAI-compatible chat interface)
- Cerebras: uses an OpenAI-compatible REST endpoint via `requests`
- Deterministic: safe local fallback when no API keys are configured
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol

import requests

from core.config.settings import get_settings

logger = logging.getLogger("pramana.llm")


class LLMProvider(Protocol):
    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 256,
        timeout: float | None = None,
        model: str | None = None,
    ) -> tuple[str, str]:
        ...


def _messages_text(messages: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for m in messages:
        role = (m.get("role") or "").strip()
        content = (m.get("content") or "").strip()
        if role or content:
            parts.append(f"{role}: {content}")
    return "\n".join(parts)


class DeterministicProvider:
    """Local fallback used when no external provider is configured."""

    name = "deterministic"

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 256,
        timeout: float | None = None,
        model: str | None = None,
    ) -> tuple[str, str]:
        text = _messages_text(messages).lower()

        # Heuristic: BaseAgent-style action selection schema.
        if "chosen_action_index" in text and "expected_cost_eth" in text:
            payload = {
                "chosen_action_index": 0,
                "rationale": "Deterministic fallback; selecting safest default.",
                "authority_basis": "safe default policy",
                "expected_cost_eth": 0.001,
                "expected_value_usd": 0.0,
                "risk_self_assessment": "low",
            }
            return json.dumps(payload), self.name

        # Heuristic: simple health-check prompt.
        if "reply with just" in text and "\"ok\"" in text:
            return json.dumps({"ok": True}), self.name

        # Generic default.
        return json.dumps({"ok": True}), self.name


@dataclass(frozen=True)
class ResilientProvider:
    """Wrap a primary provider with a fallback provider.

    This keeps CLI tooling usable even when external LLM APIs are unavailable.
    """

    primary: LLMProvider
    fallback: LLMProvider

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 256,
        timeout: float | None = None,
        model: str | None = None,
    ) -> tuple[str, str]:
        try:
            return self.primary.complete(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                model=model,
            )
        except Exception as e:
            primary_name = getattr(self.primary, "name", self.primary.__class__.__name__)
            fallback_name = getattr(self.fallback, "name", self.fallback.__class__.__name__)
            logger.warning("LLM provider '%s' failed; falling back to '%s': %s", primary_name, fallback_name, str(e)[:200])
            return self.fallback.complete(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                model=model,
            )


@dataclass(frozen=True)
class GroqProvider:
    api_key: str
    default_model: str
    default_timeout_s: float

    name: str = "groq"

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 256,
        timeout: float | None = None,
        model: str | None = None,
    ) -> tuple[str, str]:
        from groq import Groq

        client = Groq(api_key=self.api_key)
        completion = client.chat.completions.create(
            model=model or self.default_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout if timeout is not None else self.default_timeout_s,
        )
        content = completion.choices[0].message.content or ""
        return content, self.name


@dataclass(frozen=True)
class CerebrasProvider:
    api_key: str
    api_base: str
    default_model: str
    default_timeout_s: float

    name: str = "cerebras"

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 256,
        timeout: float | None = None,
        model: str | None = None,
    ) -> tuple[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # Some deployments expect api_base to include /v1, some do not.
        base = self.api_base.rstrip("/")
        urls = [base + "/chat/completions"]
        if base.endswith("/v1"):
            urls.append(base[: -len("/v1")] + "/chat/completions")
        else:
            urls.append(base + "/v1/chat/completions")

        last_exc: Exception | None = None
        for url in urls:
            try:
                r = requests.post(
                    url,
                    headers=headers,
                    json=body,
                    timeout=timeout if timeout is not None else self.default_timeout_s,
                )
                r.raise_for_status()
                data = r.json()
                return str(data["choices"][0]["message"]["content"]), self.name
            except Exception as exc:
                last_exc = exc

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Cerebras request failed")


def get_llm_provider(
    preferred: str | None = None,
    *,
    groq_api_key: str | None = None,
    groq_model: str | None = None,
    cerebras_api_key: str | None = None,
    cerebras_model: str | None = None,
    cerebras_api_base: str | None = None,
) -> LLMProvider:
    """Return an LLM provider instance.

    Selection order:
    - If `preferred` is set ("groq"|"cerebras"|"deterministic"), try that provider first.
    - Otherwise try Groq, then Cerebras.
    - If no keys are configured, return DeterministicProvider.
    """

    s = get_settings()
    preferred_norm = (preferred or "").strip().lower()

    deterministic = DeterministicProvider()

    def _build_groq() -> GroqProvider | None:
        api_key = (groq_api_key or s.GROQ_API_KEY or "").strip()
        model_name = (groq_model or s.GROQ_MODEL_L1 or "").strip()
        if not api_key:
            return None
        return GroqProvider(
            api_key=api_key,
            default_model=model_name or "llama-3.1-8b-instant",
            default_timeout_s=s.GROQ_TIMEOUT_S,
        )

    def _build_cerebras() -> CerebrasProvider | None:
        api_key = (cerebras_api_key or s.CEREBRAS_API_KEY or os.environ.get("CEREBRAS_API_KEY", "")).strip()
        model = (cerebras_model or s.CEREBRAS_MODEL_L1 or os.environ.get("CEREBRAS_MODEL_L1", "")).strip()
        api_base = (cerebras_api_base or s.CEREBRAS_API_BASE or os.environ.get(
            "CEREBRAS_API_BASE", "https://api.cerebras.ai/v1"
        )).strip()
        if not (api_key and model):
            return None
        return CerebrasProvider(
            api_key=api_key,
            api_base=api_base,
            default_model=model,
            default_timeout_s=s.CEREBRAS_TIMEOUT_S,
        )

    groq = _build_groq()
    cerebras = _build_cerebras()

    if preferred_norm == "deterministic":
        return deterministic

    if preferred_norm and preferred_norm not in ("groq", "cerebras", "deterministic"):
        logger.warning(
            "Unknown preferred LLM provider '%s' — using deterministic", preferred_norm
        )
        return deterministic

    if preferred_norm == "cerebras":
        if cerebras and groq:
            return ResilientProvider(primary=cerebras, fallback=ResilientProvider(primary=groq, fallback=deterministic))
        if cerebras:
            return ResilientProvider(primary=cerebras, fallback=deterministic)
        if groq:
            return ResilientProvider(primary=groq, fallback=deterministic)
        return deterministic

    if preferred_norm == "groq":
        if groq and cerebras:
            return ResilientProvider(primary=groq, fallback=ResilientProvider(primary=cerebras, fallback=deterministic))
        if groq:
            return ResilientProvider(primary=groq, fallback=deterministic)
        if cerebras:
            return ResilientProvider(primary=cerebras, fallback=deterministic)
        return deterministic

    # No preferred provider: try groq, then cerebras, then deterministic.
    if groq and cerebras:
        return ResilientProvider(primary=groq, fallback=ResilientProvider(primary=cerebras, fallback=deterministic))
    if groq:
        return ResilientProvider(primary=groq, fallback=deterministic)
    if cerebras:
        return ResilientProvider(primary=cerebras, fallback=deterministic)
    return deterministic
