from __future__ import annotations

from pathlib import Path

import pytest

import application.orchestrator as orchestrator
import application.run_helpers as run_helpers
from models import MFSnapshot, PortfolioSnapshot, RebalancingAction
from tests.orchestrator_support import FakeKiteClient, FakeSummaryClient, make_holding, make_settings
from tests.test_orchestrator_flow import _verdict

pytestmark = pytest.mark.anyio


async def test_run_single_company_analysis_returns_portfolio_report(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    snapshot = PortfolioSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
        total_value=10_000.0,
        available_cash=500.0,
        holdings=[make_holding("KPITTECH", 4.0, 8.0)],
    )
    monkeypatch.setattr("persistence.store.load_latest_portfolio_snapshot", lambda settings: snapshot)

    async def fake_get_company_artifact_and_verdict(**kwargs):
        return object(), _verdict("KPITTECH", verdict="BUY", action="BUY", reason="Will be overwritten."), True

    async def fake_price_contexts(**kwargs):
        return {}

    monkeypatch.setattr(orchestrator, "get_company_artifact_and_verdict", fake_get_company_artifact_and_verdict)
    monkeypatch.setattr(orchestrator, "_price_contexts", fake_price_contexts)
    monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: FakeSummaryClient())
    report = await orchestrator.run_single_company_analysis(settings=settings, ticker="KPITTECH")
    assert len(report.verdicts) == 1
    assert report.portfolio_snapshot.total_value == 10_000.0


async def test_run_single_company_analysis_handles_missing_snapshot(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(
        "persistence.store.load_latest_portfolio_snapshot",
        lambda settings: (_ for _ in ()).throw(FileNotFoundError("missing snapshot")),
    )

    async def fake_get_company_artifact_and_verdict(**kwargs):
        return object(), _verdict("INFY"), True

    async def fake_price_contexts(**kwargs):
        return {}

    monkeypatch.setattr(orchestrator, "get_company_artifact_and_verdict", fake_get_company_artifact_and_verdict)
    monkeypatch.setattr(orchestrator, "_price_contexts", fake_price_contexts)
    monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: FakeSummaryClient())
    report = await orchestrator.run_single_company_analysis(settings=settings, ticker="INFY")
    assert report.verdicts[0].tradingsymbol == "INFY"
    assert [holding.tradingsymbol for holding in report.portfolio_snapshot.holdings] == ["INFY"]


async def test_run_single_company_analysis_does_not_swallow_snapshot_parse_errors(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(
        "persistence.store.load_latest_portfolio_snapshot",
        lambda settings: (_ for _ in ()).throw(ValueError("corrupt snapshot")),
    )
    with pytest.raises(ValueError, match="corrupt snapshot"):
        await orchestrator.run_single_company_analysis(settings=settings, ticker="INFY")


async def test_run_full_analysis_emits_structured_events_in_order(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    sync_result = orchestrator.KiteSyncResult(
        profile={"user_name": "Saksham"},
        portfolio_snapshot=PortfolioSnapshot(
            fetched_at="2026-03-18T10:00:00Z",
            total_value=10_000.0,
            available_cash=0.0,
            holdings=[make_holding("KPITTECH", 4.0, 8.0)],
        ),
        portfolio_artifact=tmp_path / "portfolio.json",
        mf_snapshot=MFSnapshot(fetched_at="2026-03-18T10:00:00Z", total_value=0.0, holdings=[]),
        mf_artifact=tmp_path / "mf.json",
    )

    async def fake_sync_with_client(kite_client, settings=None, auto_login=True):
        return sync_result

    async def fake_price_contexts(**kwargs):
        return {"KPITTECH": {"52w_high": 120.0, "52w_low": 80.0, "current_vs_52w_high_pct": -10.0}}

    async def fake_get_company_artifact_and_verdict(**kwargs):
        return object(), _verdict("KPITTECH", verdict="BUY", action="BUY"), False

    monkeypatch.setattr(orchestrator, "sync_kite_data_with_client", fake_sync_with_client)
    monkeypatch.setattr(orchestrator, "build_kite_client", lambda settings: FakeKiteClient())
    monkeypatch.setattr(orchestrator, "_price_contexts", fake_price_contexts)
    monkeypatch.setattr(run_helpers, "get_company_artifact_and_verdict", fake_get_company_artifact_and_verdict)
    monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: FakeSummaryClient())
    collected = []
    await orchestrator.run_full_analysis(settings, event_callback=lambda event: collected.append(event))
    assert [event["phase"] for event in collected if event["type"] == "phase"] == [
        "kite_sync",
        "analyst",
        "rebalance",
        "summary",
    ]


def test_rebalance_only_and_gate_helpers() -> None:
    snapshot = PortfolioSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
        total_value=10_000.0,
        available_cash=500.0,
        holdings=[make_holding("KPITTECH", 8.0, 8.0), make_holding("LIQUIDBEES", 5.0, 0.0)],
    )
    report, actions = orchestrator.build_rebalance_only_report(snapshot)
    assert report.verdicts == []
    assert "rebalance-only" in report.portfolio_summary
    verdict = _verdict("BSE", verdict="SELL", action="BUY", reason="Placeholder.")
    merged = orchestrator._merge_action_into_verdict(verdict, None)
    assert merged.rebalance_action == "HOLD"
    assert orchestrator._should_gate_to_hold("UNKNOWN", True) is True
    action = RebalancingAction(
        tradingsymbol="BSE",
        action="SELL",
        current_weight_pct=14.7,
        target_weight_pct=10.0,
        drift_pct=4.7,
        rupee_amount=4700.0,
        quantity_approx=10,
        reasoning="Internal math",
        urgency="MEDIUM",
    )
    merged = orchestrator._merge_action_into_verdict(_verdict("BSE"), action)
    assert (
        merged.rebalance_reasoning
        == "Current conviction is unchanged. No rebalance action now; monitor drift versus target."
    )
