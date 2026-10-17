"""Model router: picks provider + tier, and meters every call.

Two tiers per provider (see pricing.yaml):
  small -> triage, lead scoring, short replies   (cheap, fast)
  main  -> the tool-using agent loop              (only when needed)
Every call is written to llm_calls with tokens, cache hits, latency and cost,
which is what the dashboard's "cost per conversation" is built from.
"""
from __future__ import annotations

import time
from functools import lru_cache
from typing import Any

import yaml

from .. import db
from ..config import settings
from .base import LLMResult, Provider, Usage
from .mock import MockProvider
from .providers import Anthropic, OpenAICompatible


@lru_cache(maxsize=1)
def _pricing() -> dict[str, Any]:
    return yaml.safe_load(settings.pricing_file.read_text(encoding="utf-8"))


def cost_usd(model: str, usage: Usage) -> float:
    p = _pricing()["prices"].get(model)
    if not p:
        return 0.0
    return round(
        usage.input_tokens / 1e6 * p["input"]
        + usage.cached_input_tokens / 1e6 * p.get("cached_input", p["input"])
        + usage.output_tokens / 1e6 * p["output"],
        8,
    )


def build_provider(name: str) -> Provider:
    if name == "anthropic" and settings.anthropic_api_key:
        return Anthropic(settings.anthropic_api_key)
    keys = {"openai": settings.openai_api_key, "groq": settings.groq_api_key, "gemini": settings.gemini_api_key}
    if name in keys and keys[name]:
        return OpenAICompatible(name, keys[name])
    return MockProvider()


class Router:
    def __init__(self, provider_name: str | None = None):
        self.provider = build_provider(provider_name or settings.llm_provider)
        self._fallback = MockProvider()
        tiers = _pricing()["providers"][self.provider.name]
        self.models = {
            "small": settings.small_model or tiers["small"],
            "main": settings.main_model or tiers["main"],
        }

    def _over_budget(self) -> bool:
        if not settings.daily_llm_call_budget or self.provider.name == "mock":
            return False
        today = db.now().replace(hour=0, minute=0, second=0, microsecond=0)
        n = db.rows(db.select(db.func.count().label("n")).select_from(db.llm_calls).where(
            (db.llm_calls.c.created_at >= today) & (db.llm_calls.c.provider != "mock")))[0]["n"]
        return n >= settings.daily_llm_call_budget

    def call(self, *, purpose: str, tier: str, system: str, messages: list[dict], tools=None,
             conversation_id: int | None = None, json_mode: bool = False, max_tokens: int = 600) -> LLMResult:
        provider, model = self.provider, self.models[tier]
        if self._over_budget():  # daily cap reached: answer with the free offline model instead
            provider, model = self._fallback, f"mock-{tier}"
        started = time.perf_counter()
        result = provider.complete(model, system, messages, tools=tools, json_mode=json_mode,
                                   max_tokens=max_tokens)
        db.insert_row(
            db.llm_calls,
            conversation_id=conversation_id, purpose=purpose, provider=provider.name, model=model,
            input_tokens=result.usage.input_tokens, output_tokens=result.usage.output_tokens,
            cached_tokens=result.usage.cached_input_tokens, cost_usd=cost_usd(model, result.usage),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return result
