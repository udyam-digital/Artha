from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import api.main as api_main
from reliability import FullRunFailed
from tests.api_support import make_report, make_settings, parse_sse_stream


def test_run_endpoint_streams_structured_sse_events(monkeypatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    report = make_report()

    class FakeKiteClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def fake_run_full_analysis(s, event_callback=None, sync_result=None):
        if event_callback:
            event_callback({"type": "phase", "phase": "kite_sync", "label": "Syncing…", "total": 0})
            event_callback({"type": "phase", "phase": "analyst", "label": "Analysing 1 holding(s)…", "total": 1})
            event_callback(
                {
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
                }
            )
            event_callback({"type": "phase", "phase": "rebalance", "label": "Rebalancing…", "total": 0})
            event_callback({"type": "phase", "phase": "summary", "label": "Summarising…", "total": 0})
        return report

    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    monkeypatch.setattr(api_main, "build_kite_client", lambda s: FakeKiteClient())

    async def fake_profile(_kite_client):
        return {"user_name": "ok"}

    monkeypatch.setattr(api_main, "kite_get_profile", fake_profile)
    monkeypatch.setattr(api_main, "run_full_analysis", fake_run_full_analysis)
    events = parse_sse_stream(
        TestClient(api_main.create_app()).post("/api/run", json={"rebalance_only": False}).content
    )
    assert {"status", "phase", "progress", "complete"} <= {event["event"] for event in events}
    assert [event["data"]["phase"] for event in events if event["event"] == "phase"][:2] == ["kite_sync", "analyst"]
    assert [event for event in events if event["event"] == "complete"][0]["data"]["report_id"] is not None


def test_ticker_only_run_skips_kite_auth_preflight(monkeypatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    monkeypatch.setattr(
        api_main,
        "build_kite_client",
        lambda _settings: (_ for _ in ()).throw(
            AssertionError("ticker-only runs should not require Kite auth preflight")
        ),
    )

    async def fake_run_single_company_analysis(*, settings, ticker, exchange):
        return make_report()

    monkeypatch.setattr(api_main, "run_single_company_analysis", fake_run_single_company_analysis)
    events = parse_sse_stream(
        TestClient(api_main.create_app()).post("/api/run", json={"ticker": "KPITTECH", "exchange": "NSE"}).content
    )
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
    response = TestClient(api_main.create_app()).post("/api/run", json={"rebalance_only": False})
    assert response.status_code == 503
    assert response.json()["detail"] == "Failed to initialize the live Kite session."


def test_run_endpoint_emits_error_event_on_full_run_failed(monkeypatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    class FakeKiteClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

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

    async def fake_profile(_kite_client):
        return {"user_name": "ok"}

    monkeypatch.setattr(api_main, "kite_get_profile", fake_profile)
    monkeypatch.setattr(api_main, "run_full_analysis", fake_run_full_analysis)
    error_events = [
        event
        for event in parse_sse_stream(
            TestClient(api_main.create_app()).post("/api/run", json={"rebalance_only": False}).content
        )
        if event["event"] == "error"
    ]
    assert error_events[0]["data"]["phase"] == "analyst"
    assert "analyst timed out" in error_events[0]["data"]["message"]
