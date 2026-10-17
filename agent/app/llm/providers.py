"""HTTP clients for OpenAI-compatible APIs (OpenAI, Groq, Gemini) and Anthropic.

Plain httpx calls keep the dependency list short and make every request easy
to read and debug. Each client retries transient errors twice with backoff.
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

from .base import LLMError, LLMResult, Provider, ToolCall, Usage

_TIMEOUT = httpx.Timeout(45.0, connect=10.0)
_RETRY_STATUS = {429, 500, 502, 503, 504}


def _post(url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    last = ""
    for attempt in range(3):
        try:
            r = httpx.post(url, headers=headers, json=payload, timeout=_TIMEOUT)
        except httpx.HTTPError as exc:  # network error
            last = str(exc)
        else:
            if r.status_code < 400:
                return r.json()
            last = f"{r.status_code}: {r.text[:300]}"
            if r.status_code not in _RETRY_STATUS:
                break
        time.sleep(1.5 * (attempt + 1))
    raise LLMError(last)


class OpenAICompatible(Provider):
    """Chat Completions API. Works for OpenAI, Groq and Gemini's OpenAI endpoint."""

    BASE_URLS = {
        "openai": "https://api.openai.com/v1",
        "groq": "https://api.groq.com/openai/v1",
        "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    }

    def __init__(self, name: str, api_key: str):
        self.name = name
        self.api_key = api_key
        self.base_url = self.BASE_URLS[name]

    @staticmethod
    def _to_wire(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "assistant" and m.get("tool_calls"):
                out.append({
                    "role": "assistant",
                    "content": m.get("content") or None,
                    "tool_calls": [
                        {"id": c.id, "type": "function",
                         "function": {"name": c.name, "arguments": json.dumps(c.args, ensure_ascii=False)}}
                        for c in m["tool_calls"]
                    ],
                })
            elif m["role"] == "tool":
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            else:
                out.append({"role": m["role"], "content": m["content"]})
        return out

    def complete(self, model, system, messages, tools=None, max_tokens=600, temperature=0.2, json_mode=False):
        payload: dict[str, Any] = {
            "model": model,
            "messages": self._to_wire(system, messages),
            "temperature": temperature,
        }
        # OpenAI's current parameter name; Groq/Gemini use max_tokens.
        payload["max_completion_tokens" if self.name == "openai" else "max_tokens"] = max_tokens
        # Reasoning models (Groq gpt-oss, Gemini 2.5) spend output tokens on "thinking".
        # A front-desk reply does not need it: keep it low so the answer fits in max_tokens.
        if self.name == "groq" and model.startswith("openai/gpt-oss"):
            payload["reasoning_effort"] = "low"
        elif self.name == "gemini" and "2.5-flash" in model:
            payload["reasoning_effort"] = "none"
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]
            payload["tool_choice"] = "auto"
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            data = _post(f"{self.base_url}/chat/completions", headers, payload)
        except LLMError as exc:
            if "reasoning" not in str(exc).lower() or "reasoning_effort" not in payload:
                raise
            payload.pop("reasoning_effort")  # model does not support the knob: retry without it
            data = _post(f"{self.base_url}/chat/completions", headers, payload)
        msg = data["choices"][0]["message"]
        calls = []
        for i, c in enumerate(msg.get("tool_calls") or []):
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            # some OpenAI-compatible APIs omit the id; the follow-up tool message still needs one
            calls.append(ToolCall(c.get("id") or f"call_{i}", c["function"]["name"], args))
        u = data.get("usage") or {}
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
        usage = Usage(u.get("prompt_tokens", 0) - cached, u.get("completion_tokens", 0), cached)
        return LLMResult(msg.get("content") or "", model, calls, usage)


class Anthropic(Provider):
    """Messages API with prompt caching on the (long, static) system prompt."""

    name = "anthropic"
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str):
        self.api_key = api_key

    @staticmethod
    def _to_wire(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m["role"] == "assistant":
                blocks: list[dict[str, Any]] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for c in m.get("tool_calls") or []:
                    blocks.append({"type": "tool_use", "id": c.id, "name": c.name, "input": c.args})
                out.append({"role": "assistant", "content": blocks})
            elif m["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                # consecutive tool results must share one user turn
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) \
                        and out[-1]["content"] and out[-1]["content"][0].get("type") == "tool_result":
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
            else:
                out.append({"role": "user", "content": m["content"]})
        return out

    def complete(self, model, system, messages, tools=None, max_tokens=600, temperature=0.2, json_mode=False):
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            # cache_control marks the static system prompt as cacheable:
            # repeat conversations pay ~10% of the input price for it.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": self._to_wire(messages),
        }
        if tools:
            payload["tools"] = [
                {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                for t in tools
            ]
        data = _post(self.URL, {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}, payload)
        text, calls = "", []
        for block in data.get("content", []):
            if block["type"] == "text":
                text += block["text"]
            elif block["type"] == "tool_use":
                calls.append(ToolCall(block["id"], block["name"], block.get("input") or {}))
        u = data.get("usage") or {}
        usage = Usage(
            u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0),
            u.get("output_tokens", 0),
            u.get("cache_read_input_tokens", 0),
        )
        return LLMResult(text, model, calls, usage)
