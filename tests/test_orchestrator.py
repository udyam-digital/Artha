from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import orchestrator
from config import Settings
from kite_runtime import KiteSyncResult
from models import Holding, MFSnapshot, MFHolding, PortfolioSnapshot, StockVerdict


pytestmark = pytest.mark.anyio


class FakeSummaryClient:
    def __init__(self) -> None:
        self.calls = []

    async def messages_create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="Portfolio summary")])

    @property
    def messages(self):
        return SimpleNamespace(create=self.messages_create)


class FakeKiteClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        ANTHROPIC_API_KEY="test-key",
        REPORTS_DIR=str(tmp_path / "reports"),
        KITE_DATA_DIR=str(tmp_path / "kite"),
        MODEL="claude-sonnet-4-6",
    )


def make_holding(symbol: str, current_weight: float, target_weight: float) -> Holding:
    return Holding(
        tradingsymbol=symbol,
        exchange="NSE",
        quantity=10,
        average_price=100.0,
        last_price=100.0,
        current_value=1000.0,
        current_weight_pct=current_weight,
        target_weight_pct=target_weight,
        pnl=100.0,
        pnl_pct=10.0,
        instrument_token=100 + len(symbol),
    )


async def test_run_full_analysis_excludes_etfs_and_gates_actions(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    snapshot = PortfolioSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
        total_value=10_000.0,
        available_cash=0.0,
        holdings=[
            make_holding("BSE", 14.0, 8.0),
            make_holding("KPITTECH", 4.0, 8.0),
            make_holding("LIQUIDBEES", 10.0, 0.0),
        ],
    )
    mf_snapshot = MFSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
        total_value=500.0,
        holdings=[
            MFHolding(
                tradingsymbol="AXISMIDCAP",
                fund="Axis Midcap Fund",
                folio="123",
                quantity=10.0,
                average_price=40.0,
                last_price=50.0,
                current_value=500.0,
                pnl=100.0,
                pnl_pct=25.0,
                scheme_type="Equity",
                plan="Direct",
            )
        ],
    )
    sync_result = KiteSyncResult(
        profile={"user_name": "Saksham"},
        portfolio_snapshot=snapshot,
        portfolio_artifact=tmp_path / "portfolio.json",
        mf_snapshot=mf_snapshot,
        mf_artifact=tmp_path / "mf.json",
    )

    async def fake_sync_kite_data(settings):
        return sync_result

    async def fake_kite_get_price_history(kite_client, tradingsymbol, instrument_token):
        return {
            "tradingsymbol": tradingsymbol,
            "52w_high": 120.0,
            "52w_low": 80.0,
            "current_vs_52w_high_pct": -10.0,
        }

    monkeypatch.setattr(orchestrator, "sync_kite_data", fake_sync_kite_data)
    monkeypatch.setattr(orchestrator, "build_kite_client", lambda settings: FakeKiteClient())
    monkeypatch.setattr(orchestrator, "kite_get_price_history", fake_kite_get_price_history)

    state = {"active": 0, "max_active": 0, "symbols": []}

    async def fake_analyse_stock(**kwargs):
        holding = kwargs["holding"]
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        await asyncio.sleep(0)
        state["active"] -= 1
        state["symbols"].append(holding.tradingsymbol)
        if holding.tradingsymbol == "BSE":
            return StockVerdict(
                tradingsymbol="BSE",
                company_name="BSE Ltd",
                verdict="HOLD",
                confidence="MEDIUM",
                current_price=100.0,
                buy_price=90.0,
                pnl_pct=10.0,
                thesis_intact=True,
                bull_case="Good franchise.",
                bear_case="Valuation is rich.",
                what_to_watch="Volumes",
                red_flags=[],
                rebalance_action="HOLD",
                rebalance_rupees=0.0,
                rebalance_reasoning="No action.",
                data_sources=["https://example.com/bse"],
                analysis_duration_seconds=1.0,
                error=None,
            )
        return StockVerdict(
            tradingsymbol="KPITTECH",
            company_name="KPIT Tech",
            verdict="BUY",
            confidence="HIGH",
            current_price=100.0,
            buy_price=110.0,
            pnl_pct=-9.0,
            thesis_intact=True,
            bull_case="Demand is healthy.",
            bear_case="Auto slowdown risk.",
            what_to_watch="Deal wins",
            red_flags=[],
            rebalance_action="BUY",
            rebalance_rupees=0.0,
            rebalance_reasoning="Will be overwritten.",
            data_sources=["https://example.com/kpit"],
            analysis_duration_seconds=1.2,
            error=None,
        )

    monkeypatch.setattr(orchestrator, "analyse_stock", fake_analyse_stock)
    fake_summary_client = FakeSummaryClient()
    monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: fake_summary_client)

    progress = []
    report = await orchestrator.run_full_analysis(
        settings,
        progress_callback=lambda completed, total, verdict: progress.append((completed, total, verdict.tradingsymbol)),
    )

    assert set(state["symbols"]) == {"BSE", "KPITTECH"}
    assert state["max_active"] <= 5
    assert len(report.verdicts) == 2
    assert [verdict.tradingsymbol for verdict in report.verdicts] == ["BSE", "KPITTECH"]
    assert report.verdicts[0].rebalance_action == "HOLD"
    assert report.verdicts[0].rebalance_rupees == 0.0
    assert report.verdicts[1].rebalance_action == "BUY"
    assert report.total_buy_required > 0
    assert report.total_sell_required == 0.0
    assert progress[-1][0] == 2
    assert fake_summary_client.calls[0].get("tools") is None
