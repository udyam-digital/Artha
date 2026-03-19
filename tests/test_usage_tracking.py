from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from config import Settings
from usage_tracking import format_usage_summary, record_anthropic_usage, usage_run


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        ANTHROPIC_API_KEY="test-key",
        REPORTS_DIR=str(tmp_path / "reports"),
        LLM_USAGE_DIR=str(tmp_path / "reports" / "usage"),
        KITE_DATA_DIR=str(tmp_path / "kite"),
    )


def test_record_anthropic_usage_writes_jsonl_and_updates_run_summary(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=1_000,
            output_tokens=500,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
            server_tool_use={"web_search_requests": 2},
        )
    )

    with usage_run(settings=settings, command="run") as summary:
        entry = record_anthropic_usage(
            settings=settings,
            label="portfolio_summary",
            model="claude-sonnet-4-6",
            response=response,
            metadata={"phase": "portfolio_summary"},
        )

    assert entry is not None
    assert summary.total_entries == 1
    assert summary.total_input_tokens == 1_000
    assert summary.total_output_tokens == 500
    assert summary.total_web_search_requests == 2
    assert entry["estimated_cost_usd"] == "0.030500"
    lines = summary.usage_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["command"] == "run"
    assert payload["label"] == "portfolio_summary"
    assert payload["metadata"]["phase"] == "portfolio_summary"


def test_format_usage_summary_includes_cost_and_searches(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=500,
            output_tokens=100,
            cache_read_input_tokens=200,
            cache_creation_input_tokens=0,
            server_tool_use=SimpleNamespace(web_search_requests=1),
        )
    )

    with usage_run(settings=settings, command="research") as summary:
        record_anthropic_usage(
            settings=settings,
            label="research_equity:HDFCBANK",
            model="claude-haiku-4-5",
            response=response,
            metadata={"phase": "research_equity", "ticker": "HDFCBANK"},
        )

    rendered = format_usage_summary(summary)
    assert "$0.011020" in rendered
    assert "1 call(s)" in rendered
    assert "1 web search(es)" in rendered
