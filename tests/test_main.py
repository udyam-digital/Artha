from __future__ import annotations

import argparse
from pathlib import Path

import pytest

import main
from config import Settings
from reliability import FullRunFailed


pytestmark = pytest.mark.anyio


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        ANTHROPIC_API_KEY="test-key",
        REPORTS_DIR=str(tmp_path / "reports"),
        LLM_USAGE_DIR=str(tmp_path / "reports" / "usage"),
        KITE_DATA_DIR=str(tmp_path / "kite"),
    )


async def test_handle_run_prints_failure_and_returns_nonzero(tmp_path: Path, monkeypatch, capsys) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    async def failing_run_full_analysis(settings, progress_callback=None, sync_result=None):
        raise FullRunFailed(
            phase="analyst",
            message="anthropic timeout",
            retries_used=2,
            ticker="KPITTECH",
            error_log_path=settings.llm_usage_dir / "run_errors.jsonl",
            partial_artifact_path=settings.kite_data_dir.parent / "companies" / "KPITTECH.json",
        )

    monkeypatch.setattr(main, "run_full_analysis", failing_run_full_analysis)
    args = argparse.Namespace(rebalance_only=False, ticker=None, exchange="NSE")
    rc = await main.handle_run(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "ARTHA RUN FAILED" in captured.out
    assert "Phase:                 analyst" in captured.out
    assert "Holding:               KPITTECH" in captured.out


async def test_handle_run_reuses_same_day_snapshots(tmp_path: Path, monkeypatch, capsys) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    marker = object()
    monkeypatch.setattr(main, "load_same_day_kite_sync_result", lambda settings: marker)

    async def fake_run_full_analysis(settings, progress_callback=None, sync_result=None):
        assert sync_result is marker
        raise FullRunFailed(
            phase="analyst",
            message="expected test stop",
            retries_used=0,
        )

    monkeypatch.setattr(main, "run_full_analysis", fake_run_full_analysis)
    args = argparse.Namespace(rebalance_only=False, ticker=None, exchange="NSE")
    rc = await main.handle_run(args)
    captured = capsys.readouterr()

    assert rc == 1
    assert "Using today's saved Kite snapshots." in captured.out
