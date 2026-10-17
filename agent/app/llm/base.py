"""Provider-neutral types used by the router and the agent loop."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cached_input_tokens + other.cached_input_tokens,
        )


@dataclass
class LLMResult:
    text: str
    model: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)


class LLMError(RuntimeError):
    """Raised when a provider call fails after retries."""


class Provider:
    """Interface every provider implements.

    messages use a small neutral format:
      {"role": "user"|"assistant"|"tool", "content": str,
       "tool_calls": [ToolCall]   (assistant only),
       "tool_call_id": str, "name": str   (tool only)}
    The system prompt is passed separately so providers that support
    prompt caching (Anthropic) can mark it as cacheable.
    """

    name = "base"

    def complete(
        self,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 600,
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> LLMResult:  # pragma: no cover - interface
        raise NotImplementedError
