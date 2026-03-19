from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

import api.main as api_main
from config import Settings
from models import Holding, MFSnapshot, MFHolding, PortfolioReport, PortfolioSnapshot, StockVerdict, Verdict


def make_settings(tmp_path: Path) -> Settings:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True)
    return Settings(
        ANTHROPIC_API_KEY="test-key",
        REPORTS_DIR=str(reports_dir),
        LLM_USAGE_DIR=str(reports_dir / "usage"),
        KITE_DATA_DIR=str(tmp_path / "kite"),
    )


def make_report() -> PortfolioReport:
    snapshot = PortfolioSnapshot(
        fetched_at=datetime(2026, 3, 19, tzinfo=timezone.utc),
        total_value=125000.0,
        available_cash=5000.0,
        holdings=[
            Holding(
                tradingsymbol="KPITTECH",
                exchange="NSE",
                quantity=10,
                average_price=1000.0,
                last_price=1100.0,
                current_value=11000.0,
                current_weight_pct=8.8,
                target_weight_pct=10.0,
                pnl=1000.0,
                pnl_pct=10.0,
                instrument_token=12345,
            )
        ],
    )
    return PortfolioReport(
        generated_at=datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc),
        portfolio_snapshot=snapshot,
        verdicts=[
            StockVerdict(
                tradingsymbol="KPITTECH",
                company_name="KPIT Technologies",
                verdict=Verdict.BUY,
                confidence="HIGH",
                current_price=1100.0,
                buy_price=1000.0,
                pnl_pct=10.0,
                thesis_intact=True,
                bull_case="Bull case",
                bear_case="Bear case",
                what_to_watch="Watch this",
                red_flags=["Flag 1"],
                rebalance_action="BUY",
                rebalance_rupees=2000.0,
                rebalance_reasoning="Reason",
                data_sources=["https://example.com"],
                analysis_duration_seconds=12.5,
                error="partial data",
            )
        ],
        portfolio_summary="Summary",
        total_buy_required=2000.0,
        total_sell_required=0.0,
        errors=["one issue"],
    )


def write_report(settings: Settings, report: PortfolioReport, stem: str) -> Path:
    path = settings.reports_dir / f"{stem}.json"
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return path


def test_reports_endpoint_returns_summary(monkeypatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    write_report(settings, make_report(), "20260319_120000_artha_report")
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    client = TestClient(api_main.create_app())

    response = client.get("/api/reports")

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

    latest = client.get("/api/reports/latest")
    detail = client.get("/api/reports/20260319_120000_artha_report")

    assert latest.status_code == 200
    assert detail.status_code == 200
    assert latest.json()["portfolio_summary"] == "Summary"
    assert detail.json()["verdicts"][0]["tradingsymbol"] == "KPITTECH"


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

    async def fake_profile(_kite_client):
        return {"user_name": "ok"}

    monkeypatch.setattr(api_main, "build_kite_client", lambda settings: FakeClient())
    monkeypatch.setattr(api_main, "kite_get_profile", fake_profile)
    client = TestClient(api_main.create_app())

    response = client.get("/api/price-history/KPITTECH")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    assert payload[0]["date"] == "2025-03-19"
    assert payload[1]["close"] == 115.0


def test_holdings_returns_401_with_login_url(monkeypatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    snapshot = PortfolioSnapshot(
        fetched_at=datetime(2026, 3, 19, tzinfo=timezone.utc),
        total_value=125000.0,
        available_cash=5000.0,
        holdings=[
            Holding(
                tradingsymbol="KPITTECH",
                exchange="NSE",
                quantity=10,
                average_price=1000.0,
                last_price=1100.0,
                current_value=11000.0,
                current_weight_pct=8.8,
                target_weight_pct=10.0,
                pnl=1000.0,
                pnl_pct=10.0,
                instrument_token=12345,
            )
        ],
    )
    mf_snapshot = MFSnapshot(
        fetched_at=datetime(2026, 3, 19, tzinfo=timezone.utc),
        total_value=1000.0,
        holdings=[
            MFHolding(
                tradingsymbol="MF1",
                fund="Fund 1",
                folio="folio",
                quantity=1.0,
                average_price=100.0,
                last_price=110.0,
                current_value=110.0,
                pnl=10.0,
                pnl_pct=10.0,
                scheme_type="Equity",
                plan="Direct",
            )
        ],
    )
    portfolio_path = settings.kite_data_dir / "portfolio" / "latest_snapshot.json"
    portfolio_path.parent.mkdir(parents=True, exist_ok=True)
    portfolio_path.write_text(snapshot.model_dump_json(), encoding="utf-8")
    mf_path = settings.kite_data_dir / "mf" / "latest_snapshot.json"
    mf_path.parent.mkdir(parents=True, exist_ok=True)
    mf_path.write_text(mf_snapshot.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def fake_profile(_kite_client):
        return {}

    async def fake_login(_kite_client, settings):
        return ({}, "https://kite.example/login", settings.kite_data_dir / "auth.json")

    monkeypatch.setattr(api_main, "build_kite_client", lambda settings: FakeClient())
    monkeypatch.setattr(api_main, "kite_get_profile", fake_profile)
    monkeypatch.setattr(api_main, "kite_login", fake_login)
    client = TestClient(api_main.create_app())

    response = client.get("/api/holdings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["live_status"] == "fallback"
    assert payload["live_error"]["login_url"] == "https://kite.example/login"
    assert payload["holdings"][0]["tradingsymbol"] == "KPITTECH"


def test_holdings_without_cache_still_requires_login(monkeypatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def fake_profile(_kite_client):
        return {}

    async def fake_login(_kite_client, settings):
        return ({}, "https://kite.example/login", settings.kite_data_dir / "auth.json")

    monkeypatch.setattr(api_main, "build_kite_client", lambda settings: FakeClient())
    monkeypatch.setattr(api_main, "kite_get_profile", fake_profile)
    monkeypatch.setattr(api_main, "kite_login", fake_login)
    client = TestClient(api_main.create_app())

    response = client.get("/api/holdings")

    assert response.status_code == 401
    assert response.json()["detail"]["login_url"] == "https://kite.example/login"
