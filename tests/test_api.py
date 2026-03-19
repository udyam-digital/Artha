from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

import api.main as api_main
from application.reporting import ReportParseError
from config import Settings
from models import Holding, MFSnapshot, MFHolding, PortfolioReport, PortfolioSnapshot, StockVerdict, Verdict
from reliability import FullRunFailed


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


def test_report_detail_blocks_path_traversal(monkeypatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    write_report(settings, make_report(), "20260319_120000_artha_report")
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    client = TestClient(api_main.create_app())

    response = client.get("/api/reports/../../etc/passwd")

    assert response.status_code == 404


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

    latest = client.get("/api/reports/latest")
    detail = client.get("/api/reports/some-report")

    assert latest.status_code == 500
    assert detail.status_code == 500
    assert latest.json()["detail"] == api_main.REPORT_PARSE_ERROR_DETAIL
    assert detail.json()["detail"] == api_main.REPORT_PARSE_ERROR_DETAIL


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


def test_holdings_does_not_mask_unexpected_exceptions(monkeypatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def fake_profile(_kite_client):
        return {"user_name": "ok"}

    async def broken_portfolio(*args, **kwargs):
        del args, kwargs
        raise ValueError("unexpected bug")

    monkeypatch.setattr(api_main, "build_kite_client", lambda settings: FakeClient())
    monkeypatch.setattr(api_main, "kite_get_profile", fake_profile)
    monkeypatch.setattr(api_main, "kite_get_portfolio", broken_portfolio)
    client = TestClient(api_main.create_app(), raise_server_exceptions=False)

    response = client.get("/api/holdings")

    assert response.status_code == 500


def _parse_sse_stream(raw: bytes) -> list[dict]:
    """Parse raw SSE bytes into a list of {event, data} dicts."""
    events = []
    current: dict = {}
    for line in raw.decode("utf-8").splitlines():
        if line.startswith("event:"):
            current["event"] = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            current["data"] = json.loads(line.removeprefix("data:").strip())
        elif line == "" and current:
            events.append(current)
            current = {}
    if current:
        events.append(current)
    return events


def test_run_endpoint_streams_structured_sse_events(monkeypatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    report = make_report()

    class FakeKiteClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def fake_profile(_kite_client):
        return {"user_name": "ok"}

    async def fake_run_full_analysis(s, event_callback=None, sync_result=None):
        if event_callback:
            event_callback({"type": "phase", "phase": "kite_sync", "label": "Syncing…", "total": 0})
            event_callback({"type": "phase", "phase": "analyst", "label": "Analysing 1 holding(s)…", "total": 1})
            event_callback({
                "type": "analyst_complete",
                "completed": 1,
                "total": 1,
                "ticker": "KPITTECH",
                "verdict": "BUY",
                "confidence": "HIGH",
                "thesis_intact": True,
                "pnl_pct": 10.0,
                "duration_seconds": 5.0,
                "bull_case": "Demand is healthy.",
                "red_flags": [],
            })
            event_callback({"type": "phase", "phase": "rebalance", "label": "Rebalancing…", "total": 0})
            event_callback({"type": "phase", "phase": "summary", "label": "Summarising…", "total": 0})
        return report

    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    monkeypatch.setattr(api_main, "build_kite_client", lambda s: FakeKiteClient())
    monkeypatch.setattr(api_main, "kite_get_profile", fake_profile)
    monkeypatch.setattr(api_main, "run_full_analysis", fake_run_full_analysis)

    client = TestClient(api_main.create_app())
    response = client.post("/api/run", json={"rebalance_only": False})

    assert response.status_code == 200
    events = _parse_sse_stream(response.content)
    event_names = [e["event"] for e in events]

    assert "status" in event_names
    assert "phase" in event_names
    assert "progress" in event_names
    assert "complete" in event_names

    phase_events = [e for e in events if e["event"] == "phase"]
    assert phase_events[0]["data"]["phase"] == "kite_sync"
    assert phase_events[1]["data"]["phase"] == "analyst"
    assert phase_events[1]["data"]["total"] == 1

    progress_events = [e for e in events if e["event"] == "progress"]
    assert len(progress_events) == 1
    p = progress_events[0]["data"]
    assert p["ticker"] == "KPITTECH"
    assert p["verdict"] == "BUY"
    assert p["confidence"] == "HIGH"
    assert p["thesis_intact"] is True
    assert p["red_flags"] == []
    assert "bull_case" in p

    complete_events = [e for e in events if e["event"] == "complete"]
    assert len(complete_events) == 1
    assert complete_events[0]["data"]["report_id"] is not None


def test_ticker_only_run_skips_kite_auth_preflight(monkeypatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    report = make_report()

    async def fake_single_company_analysis(*, settings, ticker, exchange):
        assert settings is not None
        assert ticker == "KPITTECH"
        assert exchange == "NSE"
        return report

    def unexpected_build_kite_client(_settings):
        raise AssertionError("ticker-only runs should not require Kite auth preflight")

    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    monkeypatch.setattr(api_main, "build_kite_client", unexpected_build_kite_client)
    monkeypatch.setattr(api_main, "run_single_company_analysis", fake_single_company_analysis)

    client = TestClient(api_main.create_app())
    response = client.post("/api/run", json={"ticker": "KPITTECH", "exchange": "NSE"})

    assert response.status_code == 200
    events = _parse_sse_stream(response.content)
    assert events[-1]["event"] == "complete"


def test_run_endpoint_sanitizes_preflight_failures(monkeypatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    class BrokenKiteClient:
        async def __aenter__(self):
            raise RuntimeError("traceback with /tmp/secret-path")

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    monkeypatch.setattr(api_main, "build_kite_client", lambda s: BrokenKiteClient())

    client = TestClient(api_main.create_app())
    response = client.post("/api/run", json={"rebalance_only": False})

    assert response.status_code == 503
    assert response.json()["detail"] == "Failed to initialize the live Kite session."


def test_run_endpoint_emits_error_event_on_full_run_failed(monkeypatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    class FakeKiteClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def fake_profile(_kite_client):
        return {"user_name": "ok"}

    async def fake_run_full_analysis(s, event_callback=None, sync_result=None):
        raise FullRunFailed(
            phase="analyst",
            message="analyst timed out",
            retries_used=3,
            ticker="KPITTECH",
            error_log_path=tmp_path / "err.json",
        )

    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    monkeypatch.setattr(api_main, "build_kite_client", lambda s: FakeKiteClient())
    monkeypatch.setattr(api_main, "kite_get_profile", fake_profile)
    monkeypatch.setattr(api_main, "run_full_analysis", fake_run_full_analysis)

    client = TestClient(api_main.create_app())
    response = client.post("/api/run", json={"rebalance_only": False})

    assert response.status_code == 200
    events = _parse_sse_stream(response.content)
    error_events = [e for e in events if e["event"] == "error"]
    assert len(error_events) == 1
    assert error_events[0]["data"]["phase"] == "analyst"
    assert "analyst timed out" in error_events[0]["data"]["message"]


def test_stream_run_sanitizes_rebalance_only_errors(monkeypatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    async def fake_sync_kite_data(*, settings):
        del settings
        raise RuntimeError("failed to reach https://internal.example/path")

    monkeypatch.setattr(api_main, "sync_kite_data", fake_sync_kite_data)

    async def consume_events() -> list[str]:
        events: list[str] = []
        async for event in api_main._stream_run(api_main.RunRequest(rebalance_only=True), settings):  # noqa: SLF001
            events.append(event)
        return events

    events = asyncio.run(consume_events())
    payloads = _parse_sse_stream("".join(events).encode("utf-8"))
    error_event = [event for event in payloads if event["event"] == "error"][0]

    assert error_event["data"]["message"] == "Rebalance-only run failed."
    assert error_event["data"]["error_code"] == api_main.STREAM_ERROR_CODE
    assert "internal.example" not in json.dumps(error_event["data"])


def test_stream_run_cancels_background_task_when_closed(monkeypatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    task_cancelled = asyncio.Event()
    task_started = asyncio.Event()

    async def fake_run_and_save(request, settings, event_callback):
        del request, settings
        event_callback({
            "type": "phase",
            "phase": "analyst",
            "label": "Running",
            "total": 1,
        })
        task_started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            task_cancelled.set()
            raise

    monkeypatch.setattr(api_main, "_run_and_save", fake_run_and_save)

    async def exercise_stream() -> None:
        stream = api_main._stream_run(api_main.RunRequest(), settings)  # noqa: SLF001
        await stream.__anext__()
        await stream.__anext__()
        await task_started.wait()
        await stream.aclose()
        assert task_cancelled.is_set()

    asyncio.run(exercise_stream())
