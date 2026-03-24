from __future__ import annotations

import json
import logging
from typing import Any

from config import Settings
from observability.usage import record_anthropic_usage
from providers.tavily import DEFAULT_TAVILY_MAX_RESULTS, tavily_search

logger = logging.getLogger(__name__)


def _extract_text(response: Any) -> str:
    text_parts: list[str] = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "text":
            text_parts.append(getattr(block, "text", ""))
    return "\n".join(text_parts).strip()


def _serialize_content_blocks(blocks: list[Any]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for block in blocks:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            serialized.append({"type": "text", "text": getattr(block, "text", "")})
            continue
        if block_type == "tool_use":
            serialized.append(
                {
                    "type": "tool_use",
                    "id": getattr(block, "id", ""),
                    "name": getattr(block, "name", ""),
                    "input": getattr(block, "input", {}) or {},
                }
            )
            continue
        serialized.append({"type": str(block_type or "unknown")})
    return serialized


def _extract_urls_from_search_result(text: str) -> list[str]:
    """Extract URLs from Tavily search result text (lines starting with 'URL: ')."""
    urls: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("URL: "):
            url = stripped[5:].strip()
            if url and url.startswith("http"):
                urls.append(url)
    return urls


def _materialize_tool_results(
    response: Any,
    *,
    config: Settings,
    search_budget_remaining: int,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    tool_results: list[dict[str, Any]] = []
    searches_used = 0
    collected_urls: list[str] = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) != "tool_use":
            continue
        tool_name = getattr(block, "name", "")
        if tool_name != "tavily_search":
            payload = json.dumps({"error": f"Unsupported tool requested: {tool_name}"}, ensure_ascii=True)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": payload,
                    "is_error": True,
                }
            )
            continue

        if searches_used >= search_budget_remaining:
            payload = json.dumps(
                {"error": f"tavily_search budget exhausted; max {config.analyst_max_searches} searches allowed."},
                ensure_ascii=True,
            )
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": payload,
                    "is_error": True,
                }
            )
            continue

        tool_input = getattr(block, "input", {}) or {}
        try:
            result = tavily_search(
                query=str(tool_input["query"]),
                max_results=int(tool_input.get("max_results", DEFAULT_TAVILY_MAX_RESULTS)),
                settings=config,
            )
            payload = result
            is_error = False
            searches_used += 1
            collected_urls.extend(_extract_urls_from_search_result(result))
        except Exception as exc:
            payload = json.dumps({"error": str(exc)}, ensure_ascii=True)
            is_error = True

        tool_results.append(
            {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": payload,
                **({"is_error": True} if is_error else {}),
            }
        )
    return tool_results, searches_used, collected_urls


def _log_response_usage(
    *,
    label: str,
    model: str,
    response: Any,
    settings: Settings,
    metadata: dict[str, Any] | None = None,
) -> None:
    record_anthropic_usage(
        settings=settings,
        label=label,
        model=model,
        response=response,
        metadata=metadata,
    )
