from __future__ import annotations

import subprocess

import mcp_server
from application.reporting import ReportListItem, ReportNotFoundError, ReportParseError
from config import Settings
from tests.test_api import make_report


def make_settings(tmp_path) -> Settings:
    return Settings(
        ANTHROPIC_API_KEY="test-key",  # pragma: allowlist secret
        REPORTS_DIR=str(tmp_path / "reports"),
        KITE_DATA_DIR=str(tmp_path / "kite"),
    )


def test_list_artha_reports_formats_rows(monkeypatch, tmp_path) -> None:
    settings = make_settings(tmp_path)
    item = ReportListItem(
        id="20260322_120000_artha_report",
        filename="20260322_120000_artha_report.json",
        generated_at=make_report().generated_at,
        total_value=125000.0,
        error_count=2,
        verdict_counts={"BUY": 1, "HOLD": 0, "SELL": 0},
    )
    monkeypatch.setattr(mcp_server, "get_settings", lambda: settings)
    monkeypatch.setattr(mcp_server, "list_report_items", lambda _settings: [item])

    result = mcp_server.list_artha_reports()

    assert "20260322_120000_artha_report" in result
    assert "BUY:1 HOLD:0 SELL:0" in result
    assert "errors:2" in result


def test_get_latest_artha_report_handles_missing(monkeypatch, tmp_path) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(mcp_server, "get_settings", lambda: settings)
    monkeypatch.setattr(
        mcp_server,
        "get_latest_report",
        lambda _settings: (_ for _ in ()).throw(ReportNotFoundError("missing")),
    )

    result = mcp_server.get_latest_artha_report()

    assert result == "No Artha reports found. Run Artha first with run_artha_analysis()."


def test_get_artha_report_handles_parse_error(monkeypatch, tmp_path) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(mcp_server, "get_settings", lambda: settings)
    monkeypatch.setattr(
        mcp_server,
        "get_report_by_id",
        lambda _settings, _report_id: (_ for _ in ()).throw(ReportParseError("bad report")),
    )

    result = mcp_server.get_artha_report("bad-id")

    assert result == "Error reading report: bad report"


def test_run_artha_analysis_returns_summary(monkeypatch, tmp_path) -> None:
    settings = make_settings(tmp_path)
    report = make_report()
    monkeypatch.setattr(mcp_server, "get_settings", lambda: settings)
    monkeypatch.setattr(
        mcp_server.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="ok", stderr=""),
    )
    monkeypatch.setattr(mcp_server, "get_latest_report", lambda _settings: report)

    result = mcp_server.run_artha_analysis(ticker="KPITTECH", rebalance_only=False)

    assert "Artha run complete." in result
    assert "BUY:1 HOLD:0 SELL:0" in result
    assert "Summary: Summary" in result


def test_run_artha_analysis_handles_timeout(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=600)

    monkeypatch.setattr(mcp_server.subprocess, "run", fake_run)

    result = mcp_server.run_artha_analysis()

    assert result == "Artha analysis timed out after 10 minutes."
