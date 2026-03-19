from __future__ import annotations

import json
import logging
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from config import Settings
from observability.telemetry import emit_span, start_span


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
    summary_path: Path
    started_at: datetime
    total_estimated_cost_usd: Decimal = _ZERO
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_input_tokens: int = 0
    total_cache_creation_input_tokens: int = 0
    total_web_search_requests: int = 0
    total_entries: int = 0
    calls_by_model: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    calls_by_phase: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    cost_by_model_usd: dict[str, Decimal] = field(default_factory=lambda: defaultdict(lambda: _ZERO))
    cost_by_phase_usd: dict[str, Decimal] = field(default_factory=lambda: defaultdict(lambda: _ZERO))
    status: str = "success"
    failed_phase: str | None = None
    failed_ticker: str | None = None
    error_message: str | None = None
    error_log_path: Path | None = None
    _span_cm: Any | None = None
    _span: Any | None = None

    def add_entry(self, entry: dict[str, Any]) -> None:
        cost = Decimal(str(entry["estimated_cost_usd"]))
        self.total_estimated_cost_usd += cost
        self.total_input_tokens += int(entry["input_tokens"])
        self.total_output_tokens += int(entry["output_tokens"])
        self.total_cache_read_input_tokens += int(entry["cache_read_input_tokens"])
        self.total_cache_creation_input_tokens += int(entry["cache_creation_input_tokens"])
        self.total_web_search_requests += int(entry["web_search_requests"])
        self.total_entries += 1

        model = str(entry["model"])
        phase = str(entry.get("metadata", {}).get("phase", "unknown"))
        self.calls_by_model[model] += 1
        self.calls_by_phase[phase] += 1
        self.cost_by_model_usd[model] += cost
        self.cost_by_phase_usd[phase] += cost


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


def _summary_path(settings: Settings) -> Path:
    return settings.llm_usage_dir / "run_summaries.jsonl"


def _error_path(settings: Settings) -> Path:
    return settings.llm_usage_dir / "run_errors.jsonl"


def _decimal_to_str(value: Decimal) -> str:
    return format(value.quantize(_USD), "f")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def estimate_input_tokens(*, messages: Any, system: Any | None = None) -> int:
    estimate = (len(str(system or "")) + len(str(messages))) // 4
    return max(estimate, 1)


def log_estimated_input_tokens(*, label: str, messages: Any, system: Any | None = None) -> int:
    estimate = estimate_input_tokens(messages=messages, system=system)
    logger.info("%s estimated input tokens: ~%s", label, estimate)
    return estimate


def _summary_record(summary: UsageRunSummary, *, completed_at: datetime) -> dict[str, Any]:
    duration_seconds = (completed_at - summary.started_at).total_seconds()
    return {
        "run_id": summary.run_id,
        "command": summary.command,
        "started_at": summary.started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_seconds": round(duration_seconds, 3),
        "usage_path": str(summary.usage_path),
        "total_estimated_cost_usd": _decimal_to_str(summary.total_estimated_cost_usd),
        "total_input_tokens": summary.total_input_tokens,
        "total_output_tokens": summary.total_output_tokens,
        "total_cache_read_input_tokens": summary.total_cache_read_input_tokens,
        "total_cache_creation_input_tokens": summary.total_cache_creation_input_tokens,
        "total_web_search_requests": summary.total_web_search_requests,
        "total_entries": summary.total_entries,
        "calls_by_model": dict(summary.calls_by_model),
        "calls_by_phase": dict(summary.calls_by_phase),
        "cost_by_model_usd": {key: _decimal_to_str(value) for key, value in summary.cost_by_model_usd.items()},
        "cost_by_phase_usd": {key: _decimal_to_str(value) for key, value in summary.cost_by_phase_usd.items()},
        "status": summary.status,
        "failed_phase": summary.failed_phase,
        "failed_ticker": summary.failed_ticker,
        "error_message": summary.error_message,
        "error_log_path": str(summary.error_log_path) if summary.error_log_path else None,
    }


@contextmanager
def usage_run(*, settings: Settings, command: str):
    usage_path = _usage_path_for_today(settings)
    summary = UsageRunSummary(
        run_id=uuid4().hex,
        command=command,
        usage_path=usage_path,
        summary_path=_summary_path(settings),
        started_at=_utc_now(),
    )
    span_cm = start_span(
        "artha.run",
        {
            "artha.run_id": summary.run_id,
            "artha.command": command,
        },
    )
    summary._span_cm = span_cm
    summary._span = span_cm.__enter__()
    token: Token[UsageRunSummary | None] = _CURRENT_RUN.set(summary)
    try:
        yield summary
    finally:
        completed_at = _utc_now()
        summary_record = _summary_record(summary, completed_at=completed_at)
        _append_jsonl(summary.summary_path, summary_record)
        if summary._span is not None:
            summary._span.set_attribute("artha.total_estimated_cost_usd", summary_record["total_estimated_cost_usd"])
            summary._span.set_attribute("artha.total_entries", summary.total_entries)
            summary._span.set_attribute("artha.total_web_search_requests", summary.total_web_search_requests)
        if summary._span_cm is not None:
            summary._span_cm.__exit__(None, None, None)
        _CURRENT_RUN.reset(token)


def get_current_usage_run() -> UsageRunSummary | None:
    return _CURRENT_RUN.get()


def record_run_error(
    *,
    settings: Settings,
    phase: str,
    error: Exception | str,
    retries_used: int,
    ticker: str | None = None,
    partial_artifact_path: Path | None = None,
) -> Path:
    run = get_current_usage_run()
    error_path = _error_path(settings)
    error_message = str(error)
    payload = {
        "timestamp": _utc_now().isoformat(),
        "run_id": run.run_id if run else None,
        "command": run.command if run else None,
        "phase": phase,
        "ticker": ticker,
        "error_type": type(error).__name__ if isinstance(error, Exception) else "RuntimeError",
        "error_message": error_message,
        "retries_used": retries_used,
        "usage_path": str(run.usage_path) if run else str(_usage_path_for_today(settings)),
        "partial_artifact_path": str(partial_artifact_path) if partial_artifact_path else None,
    }
    _append_jsonl(error_path, payload)
    if run is not None:
        run.status = "failed"
        run.failed_phase = phase
        run.failed_ticker = ticker
        run.error_message = error_message
        run.error_log_path = error_path
    emit_span(
        "artha.run_error",
        {
            "artha.run_id": run.run_id if run else None,
            "artha.command": run.command if run else None,
            "artha.failed_phase": phase,
            "artha.failed_ticker": ticker,
            "artha.retries_used": retries_used,
            "error.type": payload["error_type"],
            "error.message": error_message,
        },
    )
    return error_path


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
    _append_jsonl(usage_path, entry)

    if run is not None:
        run.add_entry(entry)

    emit_span(
        "artha.llm_call",
        {
            "artha.run_id": entry["run_id"],
            "artha.command": entry["command"],
            "artha.label": label,
            "artha.phase": entry["metadata"].get("phase"),
            "gen_ai.request.model": model,
            "gen_ai.usage.input_tokens": input_tokens,
            "gen_ai.usage.output_tokens": output_tokens,
            "artha.usage.cache_read_input_tokens": cache_read_input_tokens,
            "artha.usage.cache_creation_input_tokens": cache_creation_input_tokens,
            "artha.usage.web_search_requests": web_search_requests,
            "artha.usage.estimated_cost_usd": entry["estimated_cost_usd"],
        },
    )

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


def format_run_summary(summary_record: dict[str, Any]) -> str:
    return (
        f"{summary_record['started_at']} | {summary_record['command']} | "
        f"{summary_record.get('status', 'success')} | "
        f"${summary_record['total_estimated_cost_usd']} | "
        f"{summary_record['total_entries']} call(s) | "
        f"{summary_record['total_web_search_requests']} web search(es)"
    )


def load_recent_run_summaries(settings: Settings, *, limit: int = 10) -> list[dict[str, Any]]:
    path = _summary_path(settings)
    if not path.exists():
        return []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    records = [json.loads(line) for line in lines[-limit:]]
    records.reverse()
    return records
