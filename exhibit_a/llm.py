"""The only file that talks to a language model.

Structure over vibes: every LLM call in exhibit-a is a forced tool call with
a JSON schema, and every response is re-validated by our own dataclass
parsers (schemas.py) before anything downstream sees it. If the model
freelances, we get a SchemaError, not a surprise.

The client is a two-method seam so tests inject FakeClient and the core
never imports the anthropic SDK unless you actually use LLM mode.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

DEFAULT_MODEL = "claude-sonnet-4-6"


class LLMError(RuntimeError):
    """Anything that stops us getting valid structured output from the model."""


class LLMClient(Protocol):
    def complete_json(self, system: str, user: str,
                      tool_name: str, tool_schema: dict[str, Any],
                      max_tokens: int = 4096) -> dict[str, Any]:
        """Return the arguments of a forced tool call as a dict."""
        ...


class AnthropicClient:
    """Real client. Requires ANTHROPIC_API_KEY and the `anthropic` package
    (installed via `pip install exhibit-a-bot[llm]`)."""

    def __init__(self, model: str | None = None):
        try:
            import anthropic  # imported lazily: the core must run without it
        except ImportError as e:
            raise LLMError(
                "LLM mode needs the anthropic package: pip install 'exhibit-a-bot[llm]'"
            ) from e
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise LLMError(
                "ANTHROPIC_API_KEY is not set. LLM mode (reading issues, drafting tests) "
                "needs it; the validation engine itself does not.")
        self._sdk = anthropic.Anthropic()
        self.model = model or os.environ.get("EXHIBIT_MODEL", DEFAULT_MODEL)

    def complete_json(self, system: str, user: str,
                      tool_name: str, tool_schema: dict[str, Any],
                      max_tokens: int = 4096) -> dict[str, Any]:
        response = self._sdk.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[{
                "name": tool_name,
                "description": f"Submit the {tool_name} result.",
                "input_schema": tool_schema,
            }],
            # Forcing the tool call means the model cannot answer in prose,
            # cannot "comply" with instructions smuggled into the issue text,
            # and cannot return anything our schema check won't inspect.
            tool_choice={"type": "tool", "name": tool_name},
        )
        for block in response.content:
            if getattr(block, "type", "") == "tool_use" and block.name == tool_name:
                if not isinstance(block.input, dict):
                    raise LLMError("model returned non-object tool input")
                return block.input
        raise LLMError("model response contained no tool call")


class FakeClient:
    """Deterministic stand-in for tests and offline demos: pop from a queue."""

    def __init__(self, responses: list[dict[str, Any]]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, system: str, user: str,
                      tool_name: str, tool_schema: dict[str, Any],
                      max_tokens: int = 4096) -> dict[str, Any]:
        self.calls.append({"system": system, "user": user, "tool": tool_name})
        if not self.responses:
            raise LLMError("FakeClient ran out of scripted responses")
        return self.responses.pop(0)
