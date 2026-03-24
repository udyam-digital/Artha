from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import api.main as api_main
from application.reporting import ReportParseError
from tests.api_support import make_report, make_settings, write_report


def test_reports_endpoint_returns_summary(monkeypatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    write_report(settings, make_report(), "20260319_120000_artha_report")
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    response = TestClient(api_main.create_app()).get("/api/reports")
    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["id"] == "20260319_120000_artha_report"
    assert payload[0]["verdict_counts"] == {"BUY": 1, "HOLD": 0, "SELL": 0}
    assert payload[0]["error_count"] == 2


def test_report_detail_and_latest(monkeypatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    report = make_report()
    write_report(settings, report, "20260319_120000_artha_report")
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    client = TestClient(api_main.create_app())
    assert client.get("/api/reports/latest").json()["portfolio_summary"] == "Summary"
    assert client.get("/api/reports/20260319_120000_artha_report").json()["verdicts"][0]["tradingsymbol"] == "KPITTECH"


def test_report_detail_blocks_path_traversal(monkeypatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    write_report(settings, make_report(), "20260319_120000_artha_report")
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    assert TestClient(api_main.create_app()).get("/api/reports/../../etc/passwd").status_code == 404


def test_report_parse_errors_return_generic_500s(monkeypatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    monkeypatch.setattr(api_main, "get_latest_report", lambda _: (_ for _ in ()).throw(ReportParseError("leaked path")))
    monkeypatch.setattr(
        api_main,
        "get_report_by_id",
        lambda _settings, _report_id: (_ for _ in ()).throw(ReportParseError("raw payload")),
    )
    client = TestClient(api_main.create_app())
    assert client.get("/api/reports/latest").json()["detail"] == api_main.REPORT_PARSE_ERROR_DETAIL
    assert client.get("/api/reports/some-report").json()["detail"] == api_main.REPORT_PARSE_ERROR_DETAIL


def test_price_history_uses_latest_report_holding(monkeypatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    write_report(settings, make_report(), "20260319_120000_artha_report")
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def call_tool(self, name, payload=None):
            assert name == "get_historical_data"
            assert payload["instrument_token"] == 12345
            return {
                "candles": [
                    ["2025-03-19", 100.0, 110.0, 90.0, 105.0, 1000],
                    ["2026-03-19", 110.0, 120.0, 95.0, 115.0, 1200],
                ]
            }

    monkeypatch.setattr(api_main, "build_kite_client", lambda settings: FakeClient())

    async def fake_profile(_kite_client):
        return {"user_name": "ok"}

    monkeypatch.setattr(api_main, "kite_get_profile", fake_profile)
    payload = TestClient(api_main.create_app()).get("/api/price-history/KPITTECH").json()
    assert len(payload) == 2
    assert payload[0]["date"] == "2025-03-19"
    assert payload[1]["close"] == 115.0
