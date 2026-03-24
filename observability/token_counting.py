from __future__ import annotations

import logging
from typing import Any

from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)


def estimate_input_tokens(*, messages: Any, system: Any | None = None) -> int:
    estimate = (len(str(system or "")) + len(str(messages))) // 4
    return max(estimate, 1)


def log_estimated_input_tokens(*, label: str, messages: Any, system: Any | None = None) -> int:
    estimate = estimate_input_tokens(messages=messages, system=system)
    logger.info("%s estimated input tokens: ~%s", label, estimate)
    return estimate


async def count_input_tokens_exact(
    *,
    client: AsyncAnthropic,
    model: str,
    messages: list[dict],
    system: str | None = None,
    tools: list[dict] | None = None,
) -> int:
    """Calls the Anthropic token-counting endpoint. No tokens consumed."""
    kwargs: dict[str, Any] = {"model": model, "messages": messages}
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = tools
    result = await client.messages.count_tokens(**kwargs)
    return result.input_tokens
