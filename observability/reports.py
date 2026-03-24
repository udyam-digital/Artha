from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from config import Settings
from observability.pricing import _USD
from observability.tracking import UsageRunSummary


def _decimal_to_str(value: Decimal) -> str:
    return format(value.quantize(_USD), "f")


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
    path = settings.llm_usage_dir / "run_summaries.jsonl"
    if not path.exists():
        return []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    records = [json.loads(line) for line in lines[-limit:]]
    records.reverse()
    return records
