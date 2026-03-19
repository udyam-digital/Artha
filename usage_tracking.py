from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from config import Settings


logger = logging.getLogger(__name__)

_USD = Decimal("0.000001")
_ZERO = Decimal("0")
_WRITE_LOCK = Lock()


@dataclass(frozen=True)
class ModelPricing:
    input_per_mtok_usd: Decimal
    output_per_mtok_usd: Decimal
    cache_read_per_mtok_usd: Decimal = _ZERO
    cache_creation_per_mtok_usd: Decimal = _ZERO
    web_search_per_request_usd: Decimal = Decimal("0.01")


@dataclass
class UsageRunSummary:
    run_id: str
    command: str
    usage_path: Path
    started_at: datetime
    total_estimated_cost_usd: Decimal = _ZERO
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_input_tokens: int = 0
    total_cache_creation_input_tokens: int = 0
    total_web_search_requests: int = 0
    total_entries: int = 0

    def add_entry(self, entry: dict[str, Any]) -> None:
        self.total_estimated_cost_usd += Decimal(str(entry["estimated_cost_usd"]))
        self.total_input_tokens += int(entry["input_tokens"])
        self.total_output_tokens += int(entry["output_tokens"])
        self.total_cache_read_input_tokens += int(entry["cache_read_input_tokens"])
        self.total_cache_creation_input_tokens += int(entry["cache_creation_input_tokens"])
        self.total_web_search_requests += int(entry["web_search_requests"])
        self.total_entries += 1


_CURRENT_RUN: ContextVar[UsageRunSummary | None] = ContextVar("artha_usage_run", default=None)

_MODEL_PRICING: dict[str, ModelPricing] = {
    "claude-sonnet-4-6": ModelPricing(
        input_per_mtok_usd=Decimal("3"),
        output_per_mtok_usd=Decimal("15"),
        cache_read_per_mtok_usd=Decimal("0.30"),
        cache_creation_per_mtok_usd=Decimal("3.75"),
    ),
    "claude-sonnet-4-5": ModelPricing(
        input_per_mtok_usd=Decimal("3"),
        output_per_mtok_usd=Decimal("15"),
        cache_read_per_mtok_usd=Decimal("0.30"),
        cache_creation_per_mtok_usd=Decimal("3.75"),
    ),
    "claude-haiku-4-5": ModelPricing(
        input_per_mtok_usd=Decimal("1"),
        output_per_mtok_usd=Decimal("5"),
        cache_read_per_mtok_usd=Decimal("0.10"),
        cache_creation_per_mtok_usd=Decimal("1.25"),
    ),
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_pricing(model: str) -> ModelPricing | None:
    normalized = model.strip().lower()
    if normalized in _MODEL_PRICING:
        return _MODEL_PRICING[normalized]
    for known_model, pricing in _MODEL_PRICING.items():
        if normalized.startswith(known_model):
            return pricing
    return None


def _server_tool_use_value(server_tool_use: Any, key: str) -> int:
    if server_tool_use is None:
        return 0
    if isinstance(server_tool_use, dict):
        return int(server_tool_use.get(key, 0) or 0)
    return int(getattr(server_tool_use, key, 0) or 0)


def _usage_path_for_today(settings: Settings) -> Path:
    stamp = _utc_now().strftime("%Y%m%d")
    return settings.llm_usage_dir / f"llm_usage_{stamp}.jsonl"


def _decimal_to_str(value: Decimal) -> str:
    return format(value.quantize(_USD), "f")


@contextmanager
def usage_run(*, settings: Settings, command: str):
    usage_path = _usage_path_for_today(settings)
    usage_path.parent.mkdir(parents=True, exist_ok=True)
    summary = UsageRunSummary(
        run_id=uuid4().hex,
        command=command,
        usage_path=usage_path,
        started_at=_utc_now(),
    )
    token: Token[UsageRunSummary | None] = _CURRENT_RUN.set(summary)
    try:
        yield summary
    finally:
        _CURRENT_RUN.reset(token)


def get_current_usage_run() -> UsageRunSummary | None:
    return _CURRENT_RUN.get()


def record_anthropic_usage(
    *,
    settings: Settings,
    label: str,
    model: str,
    response: Any,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None

    run = get_current_usage_run()
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    cache_read_input_tokens = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    cache_creation_input_tokens = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    server_tool_use = getattr(usage, "server_tool_use", None)
    web_search_requests = _server_tool_use_value(server_tool_use, "web_search_requests")

    pricing = _resolve_pricing(model)
    estimated_cost_usd = _ZERO
    pricing_source = "unknown"
    if pricing is not None:
        estimated_cost_usd = (
            Decimal(input_tokens) * pricing.input_per_mtok_usd
            + Decimal(output_tokens) * pricing.output_per_mtok_usd
            + Decimal(cache_read_input_tokens) * pricing.cache_read_per_mtok_usd
            + Decimal(cache_creation_input_tokens) * pricing.cache_creation_per_mtok_usd
        ) * _USD
        estimated_cost_usd += Decimal(web_search_requests) * pricing.web_search_per_request_usd
        pricing_source = "anthropic_pricing_docs_2026_03_19"

    entry: dict[str, Any] = {
        "timestamp": _utc_now().isoformat(),
        "run_id": run.run_id if run else None,
        "command": run.command if run else None,
        "label": label,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "web_search_requests": web_search_requests,
        "estimated_cost_usd": _decimal_to_str(estimated_cost_usd),
        "pricing_source": pricing_source,
        "metadata": metadata or {},
    }

    usage_path = run.usage_path if run else _usage_path_for_today(settings)
    usage_path.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        with usage_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=True) + "\n")

    if run is not None:
        run.add_entry(entry)

    logger.info(
        "%s usage model=%s input=%s output=%s web_searches=%s est_cost_usd=%s",
        label,
        model,
        input_tokens,
        output_tokens,
        web_search_requests,
        entry["estimated_cost_usd"],
    )
    return entry


def format_usage_summary(summary: UsageRunSummary) -> str:
    return (
        "Estimated LLM cost: "
        f"${_decimal_to_str(summary.total_estimated_cost_usd)} "
        f"across {summary.total_entries} call(s), "
        f"{summary.total_input_tokens} input token(s), "
        f"{summary.total_output_tokens} output token(s), "
        f"{summary.total_web_search_requests} web search(es)"
    )
