from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from config import Settings
import observability.usage as usage_tracking
from observability.usage import (
    estimate_input_tokens,
    format_run_summary,
    format_usage_summary,
    get_current_usage_run,
    log_estimated_input_tokens,
    load_recent_run_summaries,
    record_run_error,
    record_anthropic_usage,
    usage_run,
)


class FakeSpan:
    def __init__(self) -> None:
        self.attributes = {}

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value


class FakeSpanContext:
    def __init__(self) -> None:
        self.span = FakeSpan()
        self.exited = False
        self.exit_args = None

    def __enter__(self) -> FakeSpan:
        return self.span

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self.exited = True
        self.exit_args = (exc_type, exc, tb)


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
        assert get_current_usage_run() is summary
        entry = record_anthropic_usage(
            settings=settings,
            label="portfolio_summary",
            model="claude-sonnet-4-6",
            response=response,
            metadata={"phase": "portfolio_summary"},
        )

    assert get_current_usage_run() is None
    assert entry is not None
    assert summary.total_entries == 1
    assert summary.total_input_tokens == 1_000
    assert summary.total_output_tokens == 500
    assert summary.total_web_search_requests == 2
    assert summary.calls_by_model["claude-sonnet-4-6"] == 1
    assert summary.calls_by_phase["portfolio_summary"] == 1
    assert entry["estimated_cost_usd"] == "0.030500"

    usage_lines = summary.usage_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(usage_lines) == 1
    usage_payload = json.loads(usage_lines[0])
    assert usage_payload["command"] == "run"
    assert usage_payload["label"] == "portfolio_summary"
    assert usage_payload["metadata"]["phase"] == "portfolio_summary"

    summary_lines = summary.summary_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(summary_lines) == 1
    summary_payload = json.loads(summary_lines[0])
    assert summary_payload["command"] == "run"
    assert summary_payload["cost_by_phase_usd"]["portfolio_summary"] == "0.030500"


def test_usage_run_updates_span_attributes_when_span_exists(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    fake_span_cm = FakeSpanContext()
    monkeypatch.setattr(usage_tracking, "start_span", lambda name, attributes=None: fake_span_cm)
    monkeypatch.setattr(usage_tracking, "emit_span", lambda name, attributes=None: None)
    response = SimpleNamespace(usage=SimpleNamespace(input_tokens=10, output_tokens=5))

    with usage_run(settings=settings, command="run") as summary:
        record_anthropic_usage(
            settings=settings,
            label="portfolio_summary",
            model="claude-sonnet-4-6-mini",
            response=response,
            metadata={"phase": "portfolio_summary"},
        )

    assert fake_span_cm.exited is True
    assert fake_span_cm.span.attributes["artha.total_entries"] == 1
    assert fake_span_cm.span.attributes["artha.total_web_search_requests"] == 0


def test_record_anthropic_usage_without_active_run_still_writes_daily_log(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    response = SimpleNamespace(usage=SimpleNamespace(input_tokens=0, output_tokens=0))

    entry = record_anthropic_usage(
        settings=settings,
        label="orphan_call",
        model="unknown-model",
        response=response,
        metadata={"phase": "unknown"},
    )

    assert entry is not None
    assert entry["estimated_cost_usd"] == "0.000000"
    assert entry["run_id"] is None
    assert settings.llm_usage_dir.exists()
    usage_files = list(settings.llm_usage_dir.glob("llm_usage_*.jsonl"))
    assert len(usage_files) == 1


def test_record_anthropic_usage_returns_none_when_usage_missing(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    response = SimpleNamespace()
    assert record_anthropic_usage(
        settings=settings,
        label="missing_usage",
        model="claude-haiku-4-5",
        response=response,
    ) is None


def test_estimated_input_tokens_helpers(tmp_path: Path, caplog) -> None:
    del tmp_path
    messages = [{"role": "user", "content": "hello"}]
    estimate = estimate_input_tokens(messages=messages, system="system")
    assert estimate > 0

    with caplog.at_level("INFO"):
        logged_estimate = log_estimated_input_tokens(label="[KPITTECH]", messages=messages, system="system")

    assert logged_estimate == estimate
    assert "[KPITTECH] estimated input tokens" in caplog.text


def test_formatters_and_recent_summary_loading(tmp_path: Path) -> None:
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

    with usage_run(settings=settings, command="research") as summary_one:
        record_anthropic_usage(
            settings=settings,
            label="research_equity:HDFCBANK",
            model="claude-haiku-4-5",
            response=response,
            metadata={"phase": "research_equity", "ticker": "HDFCBANK"},
        )

    with usage_run(settings=settings, command="run --ticker KPITTECH") as summary_two:
        record_anthropic_usage(
            settings=settings,
            label="analyst:KPITTECH",
            model="claude-haiku-4-5",
            response=response,
            metadata={"phase": "analyst", "ticker": "KPITTECH"},
        )

    rendered = format_usage_summary(summary_one)
    assert "$0.011020" in rendered
    assert "1 call(s)" in rendered
    assert "1 web search(es)" in rendered

    summaries = load_recent_run_summaries(settings, limit=2)
    assert [item["command"] for item in summaries] == ["run --ticker KPITTECH", "research"]
    assert "run --ticker KPITTECH" in format_run_summary(summaries[0])
    assert str(summary_two.usage_path) in summaries[0]["usage_path"]


def test_load_recent_run_summaries_returns_empty_when_missing(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    assert load_recent_run_summaries(settings, limit=5) == []


def test_record_run_error_marks_summary_failed_and_writes_error_log(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(usage_tracking, "emit_span", lambda name, attributes=None: None)

    with usage_run(settings=settings, command="run") as summary:
        error_path = record_run_error(
            settings=settings,
            phase="analyst",
            error=RuntimeError("boom"),
            retries_used=2,
            ticker="KPITTECH",
            partial_artifact_path=tmp_path / "companies" / "KPITTECH.json",
        )

    assert summary.status == "failed"
    assert summary.failed_phase == "analyst"
    assert summary.failed_ticker == "KPITTECH"
    assert summary.error_log_path == error_path
    error_payload = json.loads(error_path.read_text(encoding="utf-8").splitlines()[0])
    assert error_payload["phase"] == "analyst"
    assert error_payload["retries_used"] == 2
    summary_payload = json.loads(summary.summary_path.read_text(encoding="utf-8").splitlines()[0])
    assert summary_payload["status"] == "failed"
    assert summary_payload["failed_phase"] == "analyst"


def test_usage_run_preserves_exception_state_and_resets_context_on_summary_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = make_settings(tmp_path)
    fake_span_cm = FakeSpanContext()
    summary_path = settings.llm_usage_dir / "run_summaries.jsonl"

    monkeypatch.setattr(usage_tracking, "start_span", lambda name, attributes=None: fake_span_cm)
    monkeypatch.setattr(usage_tracking, "emit_span", lambda name, attributes=None: None)

    original_append_jsonl = usage_tracking._append_jsonl

    def flaky_append_jsonl(path: Path, payload: dict[str, object]) -> None:
        if path == summary_path:
            raise RuntimeError("disk full")
        original_append_jsonl(path, payload)

    monkeypatch.setattr(usage_tracking, "_append_jsonl", flaky_append_jsonl)

    with pytest.raises(RuntimeError, match="boom"):
        with usage_run(settings=settings, command="run") as summary:
            assert usage_tracking.get_current_usage_run() is summary
            raise RuntimeError("boom")

    assert usage_tracking.get_current_usage_run() is None
    assert fake_span_cm.exited is True
    assert fake_span_cm.exit_args is not None
    assert fake_span_cm.exit_args[0] is RuntimeError
    assert str(fake_span_cm.exit_args[1]) == "boom"
