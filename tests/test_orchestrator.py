from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import application.orchestrator as orchestrator
from config import Settings
from kite.runtime import KiteSyncResult
from models import Holding, MacroContext, MFHolding, MFSnapshot, PortfolioSnapshot, RebalancingAction, StockVerdict
from reliability import FullRunFailed, RetryFailure

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _mock_macro_context(monkeypatch):
    async def fake_macro_context():
        return MacroContext(
            cpi_headline_yoy=4.5,
            iip_growth_latest=3.2,
            gdp_growth_latest=6.4,
            as_of_date="2026-03",
            fetch_errors=[],
        )

    monkeypatch.setattr(orchestrator, "get_macro_context", fake_macro_context)


class FakeSummaryClient:
    def __init__(self) -> None:
        self.calls = []
        self.count_calls = []

    async def messages_create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text="Portfolio summary")],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )

    async def messages_count_tokens(self, **kwargs):
        self.count_calls.append(kwargs)
        return SimpleNamespace(input_tokens=222)

    @property
    def messages(self):
        return SimpleNamespace(create=self.messages_create, count_tokens=self.messages_count_tokens)


class FakeKiteClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


def make_settings(tmp_path: Path, **overrides) -> Settings:
    payload = {
        "ANTHROPIC_API_KEY": "test-key",
        "REPORTS_DIR": str(tmp_path / "reports"),
        "KITE_DATA_DIR": str(tmp_path / "kite"),
        "MODEL": "claude-sonnet-4-6",
        "ANALYST_MODEL": "claude-haiku-4-5",
        "ANALYST_PARALLELISM": 1,
        "ANALYST_MIN_START_INTERVAL_SECONDS": 0,
    }
    payload.update(overrides)
    return Settings(**payload)


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

    async def fake_sync_with_client(kite_client, *, settings=None, auto_login=True):
        return sync_result

    async def fake_kite_get_price_history(kite_client, tradingsymbol, instrument_token):
        return {
            "tradingsymbol": tradingsymbol,
            "52w_high": 120.0,
            "52w_low": 80.0,
            "current_vs_52w_high_pct": -10.0,
        }

    monkeypatch.setattr(orchestrator, "sync_kite_data_with_client", fake_sync_with_client)
    monkeypatch.setattr(orchestrator, "build_kite_client", lambda settings: FakeKiteClient())
    monkeypatch.setattr(orchestrator, "kite_get_price_history", fake_kite_get_price_history)

    state = {"active": 0, "max_active": 0, "symbols": [], "macro_contexts": []}

    async def fake_get_company_artifact_and_verdict(**kwargs):
        holding = kwargs["holding"]
        state["macro_contexts"].append(kwargs["macro_context"])
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        await asyncio.sleep(0)
        state["active"] -= 1
        state["symbols"].append(holding.tradingsymbol)
        if holding.tradingsymbol == "BSE":
            verdict = StockVerdict(
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
            return object(), verdict, False
        verdict = StockVerdict(
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
        return object(), verdict, False

    monkeypatch.setattr(orchestrator, "get_company_artifact_and_verdict", fake_get_company_artifact_and_verdict)
    fake_summary_client = FakeSummaryClient()
    monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: fake_summary_client)

    events = []
    report = await orchestrator.run_full_analysis(
        settings,
        event_callback=lambda event: events.append(event),
    )

    assert set(state["symbols"]) == {"BSE", "KPITTECH"}
    assert state["macro_contexts"] == [
        "Macro (as of 2026-03): CPI 4.50% | IIP growth 3.20% | GDP growth 6.40%",
        "Macro (as of 2026-03): CPI 4.50% | IIP growth 3.20% | GDP growth 6.40%",
    ]
    assert state["max_active"] <= settings.analyst_parallelism
    assert len(report.verdicts) == 2
    assert [verdict.tradingsymbol for verdict in report.verdicts] == ["BSE", "KPITTECH"]
    assert report.verdicts[0].rebalance_action == "HOLD"
    assert report.verdicts[0].rebalance_rupees == 0.0
    assert report.verdicts[0].rebalance_reasoning == (
        "Current conviction is unchanged. No rebalance action now; monitor drift versus target."
    )
    assert report.verdicts[1].rebalance_action == "BUY"
    assert report.verdicts[1].rebalance_reasoning == (
        "Underweight versus target. Current conviction supports adding more."
    )
    assert report.total_buy_required > 0
    assert report.total_sell_required == 0.0
    analyst_events = [e for e in events if e["type"] == "analyst_complete"]
    assert analyst_events[-1]["completed"] == 2
    phase_types = [e["phase"] for e in events if e["type"] == "phase"]
    assert "kite_sync" in phase_types
    assert "analyst" in phase_types
    assert "rebalance" in phase_types
    assert "summary" in phase_types
    assert fake_summary_client.calls[0].get("tools") is None
    assert fake_summary_client.calls[0]["model"] == "claude-sonnet-4-6"
    assert fake_summary_client.count_calls[0]["model"] == "claude-sonnet-4-6"


async def test_run_full_analysis_degrades_when_macro_context_fails(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    snapshot = PortfolioSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
        total_value=10_000.0,
        available_cash=0.0,
        holdings=[make_holding("BSE", 8.0, 8.0)],
    )
    sync_result = KiteSyncResult(
        profile={"user_name": "Saksham"},
        portfolio_snapshot=snapshot,
        portfolio_artifact=tmp_path / "portfolio.json",
        mf_snapshot=MFSnapshot(fetched_at="2026-03-18T10:00:00Z", total_value=0.0, holdings=[]),
        mf_artifact=tmp_path / "mf.json",
    )

    async def fake_sync_with_client(kite_client, *, settings=None, auto_login=True):
        return sync_result

    monkeypatch.setattr(orchestrator, "sync_kite_data_with_client", fake_sync_with_client)
    monkeypatch.setattr(orchestrator, "build_kite_client", lambda settings: FakeKiteClient())

    async def fake_kite_get_price_history(kite_client, tradingsymbol, instrument_token):
        return {"52w_high": 120.0, "52w_low": 80.0, "current_vs_52w_high_pct": -10.0}

    monkeypatch.setattr(orchestrator, "kite_get_price_history", fake_kite_get_price_history)

    async def failing_macro_context():
        return MacroContext(fetch_errors=["cpi: unavailable", "iip: unavailable", "gdp: unavailable"])

    seen = {"macro_context": None}

    async def fake_get_company_artifact_and_verdict(**kwargs):
        seen["macro_context"] = kwargs["macro_context"]
        return (
            object(),
            StockVerdict(
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
            ),
            False,
        )

    monkeypatch.setattr(orchestrator, "get_macro_context", failing_macro_context)
    monkeypatch.setattr(orchestrator, "get_company_artifact_and_verdict", fake_get_company_artifact_and_verdict)
    monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: FakeSummaryClient())

    report = await orchestrator.run_full_analysis(settings)

    assert seen["macro_context"] == ""
    assert report.errors == ["cpi: unavailable", "iip: unavailable", "gdp: unavailable"]


async def test_run_full_analysis_reuses_fresh_company_cache(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    snapshot = PortfolioSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
        total_value=10_000.0,
        available_cash=0.0,
        holdings=[make_holding("KPITTECH", 4.0, 8.0)],
    )
    mf_snapshot = MFSnapshot(fetched_at="2026-03-18T10:00:00Z", total_value=0.0, holdings=[])
    sync_result = KiteSyncResult(
        profile={"user_name": "Saksham"},
        portfolio_snapshot=snapshot,
        portfolio_artifact=tmp_path / "portfolio.json",
        mf_snapshot=mf_snapshot,
        mf_artifact=tmp_path / "mf.json",
    )

    async def fake_sync_with_client(kite_client, *, settings=None, auto_login=True):
        return sync_result

    async def fake_kite_get_price_history(kite_client, tradingsymbol, instrument_token):
        return {"52w_high": 120.0, "52w_low": 80.0, "current_vs_52w_high_pct": -10.0}

    monkeypatch.setattr(orchestrator, "sync_kite_data_with_client", fake_sync_with_client)
    monkeypatch.setattr(orchestrator, "build_kite_client", lambda settings: FakeKiteClient())
    monkeypatch.setattr(orchestrator, "kite_get_price_history", fake_kite_get_price_history)

    calls = {"count": 0}

    async def fake_get_company_artifact_and_verdict(**kwargs):
        calls["count"] += 1
        return (
            object(),
            StockVerdict(
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
            ),
            True,
        )

    monkeypatch.setattr(orchestrator, "get_company_artifact_and_verdict", fake_get_company_artifact_and_verdict)
    monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: FakeSummaryClient())

    report = await orchestrator.run_full_analysis(settings)
    assert calls["count"] == 1
    assert report.verdicts[0].analysis_duration_seconds == 0.0


async def test_run_full_analysis_respects_configured_analyst_parallelism(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path, ANALYST_PARALLELISM=2, ANALYST_MIN_START_INTERVAL_SECONDS=0)
    snapshot = PortfolioSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
        total_value=10_000.0,
        available_cash=0.0,
        holdings=[
            make_holding("BSE", 14.0, 8.0),
            make_holding("KPITTECH", 4.0, 8.0),
            make_holding("HDFCBANK", 7.0, 8.0),
        ],
    )
    sync_result = KiteSyncResult(
        profile={"user_name": "Saksham"},
        portfolio_snapshot=snapshot,
        portfolio_artifact=tmp_path / "portfolio.json",
        mf_snapshot=MFSnapshot(fetched_at="2026-03-18T10:00:00Z", total_value=0.0, holdings=[]),
        mf_artifact=tmp_path / "mf.json",
    )

    async def fake_sync_with_client(kite_client, *, settings=None, auto_login=True):
        return sync_result

    async def fake_kite_get_price_history(kite_client, tradingsymbol, instrument_token):
        return {"52w_high": 120.0, "52w_low": 80.0, "current_vs_52w_high_pct": -10.0}

    state = {"active": 0, "max_active": 0}

    async def fake_get_company_artifact_and_verdict(**kwargs):
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        await asyncio.sleep(0)
        state["active"] -= 1
        symbol = kwargs["holding"].tradingsymbol
        return (
            object(),
            StockVerdict(
                tradingsymbol=symbol,
                company_name=symbol,
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
                data_sources=["https://example.com"],
                analysis_duration_seconds=1.0,
                error=None,
            ),
            False,
        )

    monkeypatch.setattr(orchestrator, "sync_kite_data_with_client", fake_sync_with_client)
    monkeypatch.setattr(orchestrator, "build_kite_client", lambda settings: FakeKiteClient())
    monkeypatch.setattr(orchestrator, "kite_get_price_history", fake_kite_get_price_history)
    monkeypatch.setattr(orchestrator, "get_company_artifact_and_verdict", fake_get_company_artifact_and_verdict)
    monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: FakeSummaryClient())

    await orchestrator.run_full_analysis(settings)
    assert state["max_active"] <= 2


async def test_run_full_analysis_paces_analyst_starts(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path, ANALYST_PARALLELISM=3, ANALYST_MIN_START_INTERVAL_SECONDS=0.05)
    snapshot = PortfolioSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
        total_value=10_000.0,
        available_cash=0.0,
        holdings=[
            make_holding("BSE", 14.0, 8.0),
            make_holding("KPITTECH", 4.0, 8.0),
            make_holding("HDFCBANK", 7.0, 8.0),
        ],
    )
    sync_result = KiteSyncResult(
        profile={"user_name": "Saksham"},
        portfolio_snapshot=snapshot,
        portfolio_artifact=tmp_path / "portfolio.json",
        mf_snapshot=MFSnapshot(fetched_at="2026-03-18T10:00:00Z", total_value=0.0, holdings=[]),
        mf_artifact=tmp_path / "mf.json",
    )

    async def fake_sync_with_client(kite_client, *, settings=None, auto_login=True):
        return sync_result

    async def fake_kite_get_price_history(kite_client, tradingsymbol, instrument_token):
        return {"52w_high": 120.0, "52w_low": 80.0, "current_vs_52w_high_pct": -10.0}

    start_times = []

    async def fake_get_company_artifact_and_verdict(**kwargs):
        start_times.append(asyncio.get_running_loop().time())
        symbol = kwargs["holding"].tradingsymbol
        return (
            object(),
            StockVerdict(
                tradingsymbol=symbol,
                company_name=symbol,
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
                data_sources=["https://example.com"],
                analysis_duration_seconds=1.0,
                error=None,
            ),
            False,
        )

    monkeypatch.setattr(orchestrator, "sync_kite_data_with_client", fake_sync_with_client)
    monkeypatch.setattr(orchestrator, "build_kite_client", lambda settings: FakeKiteClient())
    monkeypatch.setattr(orchestrator, "kite_get_price_history", fake_kite_get_price_history)
    monkeypatch.setattr(orchestrator, "get_company_artifact_and_verdict", fake_get_company_artifact_and_verdict)
    monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: FakeSummaryClient())

    await orchestrator.run_full_analysis(settings)
    assert len(start_times) == 3
    # Allow a small amount of event-loop scheduling jitter while still verifying staggered starts.
    assert start_times[1] - start_times[0] >= 0.04
    assert start_times[2] - start_times[1] >= 0.04


async def test_build_portfolio_summary_falls_back_when_exact_count_fails(tmp_path: Path, monkeypatch, caplog) -> None:
    settings = make_settings(tmp_path)
    client = FakeSummaryClient()

    async def broken_count_tokens(**kwargs):
        raise RuntimeError("count failed")

    monkeypatch.setattr(client, "messages_count_tokens", broken_count_tokens)

    with caplog.at_level("WARNING"):
        summary = await orchestrator._build_portfolio_summary(
            client=client,  # type: ignore[arg-type]
            settings=settings,
            verdicts=[],
            snapshot=PortfolioSnapshot(
                fetched_at="2026-03-18T10:00:00Z",
                total_value=1_000.0,
                available_cash=0.0,
                holdings=[],
            ),
            mf_symbols=[],
            errors=[],
        )

    assert summary == "Portfolio summary"
    assert "exact token counting failed" in caplog.text


async def test_run_full_analysis_uses_token_budget_manager(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    snapshot = PortfolioSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
        total_value=10_000.0,
        available_cash=0.0,
        holdings=[make_holding("KPITTECH", 4.0, 8.0)],
    )
    sync_result = KiteSyncResult(
        profile={"user_name": "Saksham"},
        portfolio_snapshot=snapshot,
        portfolio_artifact=tmp_path / "portfolio.json",
        mf_snapshot=MFSnapshot(fetched_at="2026-03-18T10:00:00Z", total_value=0.0, holdings=[]),
        mf_artifact=tmp_path / "mf.json",
    )

    async def fake_sync_with_client(kite_client, *, settings=None, auto_login=True):
        return sync_result

    async def fake_kite_get_price_history(kite_client, tradingsymbol, instrument_token):
        return {"52w_high": 120.0, "52w_low": 80.0, "current_vs_52w_high_pct": -10.0}

    budget_calls = []

    class FakeBudgetManager:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def acquire(self, *, estimated_input_tokens: int, estimated_output_tokens: int) -> None:
            budget_calls.append((estimated_input_tokens, estimated_output_tokens))

        def record_actual(self, *, input_tokens: int, output_tokens: int) -> None:
            return None

    async def fake_get_company_artifact_and_verdict(**kwargs):
        return (
            object(),
            StockVerdict(
                tradingsymbol="KPITTECH",
                company_name="KPITTECH",
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
                data_sources=["https://example.com"],
                analysis_duration_seconds=1.0,
                error=None,
            ),
            False,
        )

    monkeypatch.setattr(orchestrator, "TokenBudgetManager", FakeBudgetManager)
    monkeypatch.setattr(orchestrator, "sync_kite_data_with_client", fake_sync_with_client)
    monkeypatch.setattr(orchestrator, "build_kite_client", lambda settings: FakeKiteClient())
    monkeypatch.setattr(orchestrator, "kite_get_price_history", fake_kite_get_price_history)
    monkeypatch.setattr(orchestrator, "get_company_artifact_and_verdict", fake_get_company_artifact_and_verdict)
    monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: FakeSummaryClient())

    await orchestrator.run_full_analysis(settings)
    assert budget_calls == [(4000, 1500)]


async def test_run_full_analysis_continues_when_price_history_is_missing(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    snapshot = PortfolioSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
        total_value=10_000.0,
        available_cash=0.0,
        holdings=[make_holding("TIPSMUSIC", 4.0, 8.0)],
    )
    sync_result = KiteSyncResult(
        profile={"user_name": "Saksham"},
        portfolio_snapshot=snapshot,
        portfolio_artifact=tmp_path / "portfolio.json",
        mf_snapshot=MFSnapshot(fetched_at="2026-03-18T10:00:00Z", total_value=0.0, holdings=[]),
        mf_artifact=tmp_path / "mf.json",
    )

    async def fake_sync_with_client(kite_client, *, settings=None, auto_login=True):
        return sync_result

    async def fake_kite_get_price_history(kite_client, tradingsymbol, instrument_token):
        from kite.tools import ToolExecutionError

        raise ToolExecutionError("No historical data available for TIPSMUSIC")

    captured_price_contexts = []

    async def fake_get_company_artifact_and_verdict(**kwargs):
        captured_price_contexts.append(kwargs["price_context"])
        return (
            object(),
            StockVerdict(
                tradingsymbol="TIPSMUSIC",
                company_name="Tips Music",
                verdict="HOLD",
                confidence="MEDIUM",
                current_price=100.0,
                buy_price=90.0,
                pnl_pct=10.0,
                thesis_intact=True,
                bull_case="Catalog optionality.",
                bear_case="Execution risk.",
                what_to_watch="Royalty growth",
                red_flags=[],
                rebalance_action="HOLD",
                rebalance_rupees=0.0,
                rebalance_reasoning="No action.",
                data_sources=["https://example.com"],
                analysis_duration_seconds=1.0,
                error=None,
            ),
            False,
        )

    monkeypatch.setattr(orchestrator, "build_kite_client", lambda settings: FakeKiteClient())
    monkeypatch.setattr(orchestrator, "sync_kite_data_with_client", fake_sync_with_client)
    monkeypatch.setattr(orchestrator, "kite_get_price_history", fake_kite_get_price_history)
    monkeypatch.setattr(orchestrator, "get_company_artifact_and_verdict", fake_get_company_artifact_and_verdict)
    monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: FakeSummaryClient())

    report = await orchestrator.run_full_analysis(settings)

    assert report.verdicts[0].tradingsymbol == "TIPSMUSIC"
    assert captured_price_contexts[0]["52w_high"] == 0.0
    assert captured_price_contexts[0]["price_change_1y_pct"] == 0.0


async def test_run_full_analysis_fails_fast_and_logs_error(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    snapshot = PortfolioSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
        total_value=10_000.0,
        available_cash=0.0,
        holdings=[make_holding("KPITTECH", 4.0, 8.0)],
    )
    mf_snapshot = MFSnapshot(fetched_at="2026-03-18T10:00:00Z", total_value=0.0, holdings=[])
    sync_result = KiteSyncResult(
        profile={"user_name": "Saksham"},
        portfolio_snapshot=snapshot,
        portfolio_artifact=tmp_path / "portfolio.json",
        mf_snapshot=mf_snapshot,
        mf_artifact=tmp_path / "mf.json",
    )

    async def fake_sync_with_client(kite_client, *, settings=None, auto_login=True):
        return sync_result

    async def fake_kite_get_price_history(kite_client, tradingsymbol, instrument_token):
        return {"52w_high": 120.0, "52w_low": 80.0, "current_vs_52w_high_pct": -10.0}

    async def failing_get_company_artifact_and_verdict(**kwargs):
        raise TimeoutError("anthropic timeout")

    monkeypatch.setattr(orchestrator, "sync_kite_data_with_client", fake_sync_with_client)
    monkeypatch.setattr(orchestrator, "build_kite_client", lambda settings: FakeKiteClient())
    monkeypatch.setattr(orchestrator, "kite_get_price_history", fake_kite_get_price_history)
    monkeypatch.setattr(orchestrator, "get_company_artifact_and_verdict", failing_get_company_artifact_and_verdict)
    monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: FakeSummaryClient())

    with pytest.raises(FullRunFailed) as exc_info:
        await orchestrator.run_full_analysis(settings)

    exc = exc_info.value
    assert exc.phase == "analyst"
    assert exc.ticker == "KPITTECH"
    assert exc.error_log_path is not None
    assert exc.error_log_path.exists()


async def test_run_full_analysis_fails_fast_on_kite_sync_retry_failure(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)

    async def fake_run_with_retries(func, *, phase, **kwargs):
        if phase == "kite_sync":
            raise RetryFailure(
                phase="kite_sync",
                cause=TimeoutError("kite timeout"),
                retries_used=2,
            )
        return await func()

    monkeypatch.setattr(orchestrator, "run_with_retries", fake_run_with_retries)

    with pytest.raises(FullRunFailed) as exc_info:
        await orchestrator.run_full_analysis(settings)

    assert exc_info.value.phase == "kite_sync"
    assert exc_info.value.error_log_path is not None
    assert exc_info.value.error_log_path.exists()


async def test_run_full_analysis_fails_fast_on_price_history_retry_failure(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    snapshot = PortfolioSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
        total_value=10_000.0,
        available_cash=0.0,
        holdings=[make_holding("KPITTECH", 4.0, 8.0)],
    )
    sync_result = KiteSyncResult(
        profile={"user_name": "Saksham"},
        portfolio_snapshot=snapshot,
        portfolio_artifact=tmp_path / "portfolio.json",
        mf_snapshot=MFSnapshot(fetched_at="2026-03-18T10:00:00Z", total_value=0.0, holdings=[]),
        mf_artifact=tmp_path / "mf.json",
    )

    async def fake_sync_with_client(kite_client, *, settings=None, auto_login=True):
        return sync_result

    async def failing_price_contexts(**kwargs):
        raise RetryFailure(
            phase="price_history",
            cause=TimeoutError("price timeout"),
            retries_used=1,
            ticker="KPITTECH",
        )

    monkeypatch.setattr(orchestrator, "sync_kite_data_with_client", fake_sync_with_client)
    monkeypatch.setattr(orchestrator, "_price_contexts", failing_price_contexts)
    monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: FakeSummaryClient())

    with pytest.raises(FullRunFailed) as exc_info:
        await orchestrator.run_full_analysis(settings)

    assert exc_info.value.phase == "price_history"
    assert exc_info.value.ticker == "KPITTECH"


async def test_run_full_analysis_fails_on_verdict_error_payload(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    snapshot = PortfolioSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
        total_value=10_000.0,
        available_cash=0.0,
        holdings=[make_holding("KPITTECH", 4.0, 8.0)],
    )
    sync_result = KiteSyncResult(
        profile={"user_name": "Saksham"},
        portfolio_snapshot=snapshot,
        portfolio_artifact=tmp_path / "portfolio.json",
        mf_snapshot=MFSnapshot(fetched_at="2026-03-18T10:00:00Z", total_value=0.0, holdings=[]),
        mf_artifact=tmp_path / "mf.json",
    )

    async def fake_sync_with_client(kite_client, *, settings=None, auto_login=True):
        return sync_result

    async def fake_kite_get_price_history(kite_client, tradingsymbol, instrument_token):
        return {"52w_high": 120.0, "52w_low": 80.0, "current_vs_52w_high_pct": -10.0}

    async def fake_get_company_artifact_and_verdict(**kwargs):
        return (
            object(),
            StockVerdict(
                tradingsymbol="KPITTECH",
                company_name="KPIT Tech",
                verdict="HOLD",
                confidence="MEDIUM",
                current_price=100.0,
                buy_price=100.0,
                pnl_pct=0.0,
                thesis_intact=True,
                bull_case="Good franchise.",
                bear_case="Needs review.",
                what_to_watch="Deal wins",
                red_flags=[],
                rebalance_action="HOLD",
                rebalance_rupees=0.0,
                rebalance_reasoning="No action.",
                data_sources=["https://example.com"],
                analysis_duration_seconds=1.0,
                error="analyst returned fallback",
            ),
            False,
        )

    monkeypatch.setattr(orchestrator, "sync_kite_data_with_client", fake_sync_with_client)
    monkeypatch.setattr(orchestrator, "build_kite_client", lambda settings: FakeKiteClient())
    monkeypatch.setattr(orchestrator, "kite_get_price_history", fake_kite_get_price_history)
    monkeypatch.setattr(orchestrator, "get_company_artifact_and_verdict", fake_get_company_artifact_and_verdict)
    monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: FakeSummaryClient())

    with pytest.raises(FullRunFailed) as exc_info:
        await orchestrator.run_full_analysis(settings)

    assert exc_info.value.phase == "analyst"
    assert exc_info.value.ticker == "KPITTECH"


async def test_run_full_analysis_fails_fast_on_summary_retry_failure(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    snapshot = PortfolioSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
        total_value=10_000.0,
        available_cash=0.0,
        holdings=[make_holding("KPITTECH", 4.0, 8.0)],
    )
    sync_result = KiteSyncResult(
        profile={"user_name": "Saksham"},
        portfolio_snapshot=snapshot,
        portfolio_artifact=tmp_path / "portfolio.json",
        mf_snapshot=MFSnapshot(fetched_at="2026-03-18T10:00:00Z", total_value=0.0, holdings=[]),
        mf_artifact=tmp_path / "mf.json",
    )

    async def fake_sync_with_client(kite_client, *, settings=None, auto_login=True):
        return sync_result

    async def fake_kite_get_price_history(kite_client, tradingsymbol, instrument_token):
        return {"52w_high": 120.0, "52w_low": 80.0, "current_vs_52w_high_pct": -10.0}

    async def fake_get_company_artifact_and_verdict(**kwargs):
        return (
            object(),
            StockVerdict(
                tradingsymbol="KPITTECH",
                company_name="KPIT Tech",
                verdict="BUY",
                confidence="HIGH",
                current_price=100.0,
                buy_price=90.0,
                pnl_pct=10.0,
                thesis_intact=True,
                bull_case="Good franchise.",
                bear_case="Auto slowdown risk.",
                what_to_watch="Deal wins",
                red_flags=[],
                rebalance_action="BUY",
                rebalance_rupees=0.0,
                rebalance_reasoning="No action.",
                data_sources=["https://example.com"],
                analysis_duration_seconds=1.0,
                error=None,
            ),
            False,
        )

    async def fake_run_with_retries(func, *, phase, **kwargs):
        if phase == "portfolio_summary":
            raise RetryFailure(
                phase="portfolio_summary",
                cause=TimeoutError("summary timeout"),
                retries_used=2,
            )
        return await func()

    monkeypatch.setattr(orchestrator, "sync_kite_data_with_client", fake_sync_with_client)
    monkeypatch.setattr(orchestrator, "build_kite_client", lambda settings: FakeKiteClient())
    monkeypatch.setattr(orchestrator, "kite_get_price_history", fake_kite_get_price_history)
    monkeypatch.setattr(orchestrator, "get_company_artifact_and_verdict", fake_get_company_artifact_and_verdict)
    monkeypatch.setattr(orchestrator, "run_with_retries", fake_run_with_retries)
    monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: FakeSummaryClient())

    with pytest.raises(FullRunFailed) as exc_info:
        await orchestrator.run_full_analysis(settings)

    assert exc_info.value.phase == "portfolio_summary"


async def test_run_full_analysis_with_saved_sync_result_still_fetches_price_context(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_settings(tmp_path)
    snapshot = PortfolioSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
        total_value=10_000.0,
        available_cash=0.0,
        holdings=[make_holding("KPITTECH", 4.0, 8.0)],
    )
    sync_result = KiteSyncResult(
        profile={"user_name": "Saksham"},
        portfolio_snapshot=snapshot,
        portfolio_artifact=tmp_path / "portfolio.json",
        mf_snapshot=MFSnapshot(fetched_at="2026-03-18T10:00:00Z", total_value=0.0, holdings=[]),
        mf_artifact=tmp_path / "mf.json",
    )
    captured_price_contexts = []

    async def fake_price_contexts(**kwargs):
        return {
            "KPITTECH": {
                "52w_high": 120.0,
                "52w_low": 80.0,
                "current_vs_52w_high_pct": -10.0,
            }
        }

    async def fake_get_company_artifact_and_verdict(**kwargs):
        captured_price_contexts.append(kwargs["price_context"])
        return (
            object(),
            StockVerdict(
                tradingsymbol="KPITTECH",
                company_name="KPIT Tech",
                verdict="BUY",
                confidence="HIGH",
                current_price=100.0,
                buy_price=90.0,
                pnl_pct=10.0,
                thesis_intact=True,
                bull_case="Good franchise.",
                bear_case="Auto slowdown risk.",
                what_to_watch="Deal wins",
                red_flags=[],
                rebalance_action="BUY",
                rebalance_rupees=0.0,
                rebalance_reasoning="No action.",
                data_sources=["https://example.com"],
                analysis_duration_seconds=1.0,
                error=None,
            ),
            False,
        )

    monkeypatch.setattr(orchestrator, "_price_contexts", fake_price_contexts)
    monkeypatch.setattr(orchestrator, "get_company_artifact_and_verdict", fake_get_company_artifact_and_verdict)
    monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: FakeSummaryClient())

    report = await orchestrator.run_full_analysis(settings, sync_result=sync_result)

    assert report.verdicts[0].tradingsymbol == "KPITTECH"
    assert captured_price_contexts[0]["52w_high"] == 120.0


async def test_run_full_analysis_cancels_other_tasks_after_fatal_failure(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path, ANALYST_PARALLELISM=2)
    snapshot = PortfolioSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
        total_value=10_000.0,
        available_cash=0.0,
        holdings=[make_holding("KPITTECH", 4.0, 8.0), make_holding("BSE", 14.0, 8.0)],
    )
    sync_result = KiteSyncResult(
        profile={"user_name": "Saksham"},
        portfolio_snapshot=snapshot,
        portfolio_artifact=tmp_path / "portfolio.json",
        mf_snapshot=MFSnapshot(fetched_at="2026-03-18T10:00:00Z", total_value=0.0, holdings=[]),
        mf_artifact=tmp_path / "mf.json",
    )
    cancelled = asyncio.Event()

    async def fake_sync_with_client(kite_client, *, settings=None, auto_login=True):
        return sync_result

    async def fake_kite_get_price_history(kite_client, tradingsymbol, instrument_token):
        return {"52w_high": 120.0, "52w_low": 80.0, "current_vs_52w_high_pct": -10.0}

    async def fake_get_company_artifact_and_verdict(**kwargs):
        holding = kwargs["holding"]
        if holding.tradingsymbol == "KPITTECH":
            raise TimeoutError("anthropic timeout")
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        raise AssertionError("Expected cancellation")

    monkeypatch.setattr(orchestrator, "sync_kite_data_with_client", fake_sync_with_client)
    monkeypatch.setattr(orchestrator, "build_kite_client", lambda settings: FakeKiteClient())
    monkeypatch.setattr(orchestrator, "kite_get_price_history", fake_kite_get_price_history)
    monkeypatch.setattr(orchestrator, "get_company_artifact_and_verdict", fake_get_company_artifact_and_verdict)
    monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: FakeSummaryClient())

    with pytest.raises(FullRunFailed):
        await orchestrator.run_full_analysis(settings)

    assert cancelled.is_set()


async def test_run_single_company_analysis_returns_portfolio_report(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    snapshot = PortfolioSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
        total_value=10_000.0,
        available_cash=500.0,
        holdings=[make_holding("KPITTECH", 4.0, 8.0)],
    )

    monkeypatch.setattr(
        "persistence.store.load_latest_portfolio_snapshot",
        lambda settings: snapshot,
    )

    async def fake_get_company_artifact_and_verdict(**kwargs):
        return (
            object(),
            StockVerdict(
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
            ),
            True,
        )

    monkeypatch.setattr(orchestrator, "get_company_artifact_and_verdict", fake_get_company_artifact_and_verdict)

    async def fake_price_contexts(**kwargs):
        return {}

    monkeypatch.setattr(orchestrator, "_price_contexts", fake_price_contexts)
    monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: FakeSummaryClient())

    report = await orchestrator.run_single_company_analysis(settings=settings, ticker="KPITTECH")
    assert len(report.verdicts) == 1
    assert report.verdicts[0].tradingsymbol == "KPITTECH"
    assert report.portfolio_snapshot.total_value == 10_000.0


async def test_run_single_company_analysis_handles_missing_snapshot(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)

    monkeypatch.setattr(
        "persistence.store.load_latest_portfolio_snapshot",
        lambda settings: (_ for _ in ()).throw(FileNotFoundError("missing snapshot")),
    )

    async def fake_get_company_artifact_and_verdict(**kwargs):
        return (
            object(),
            StockVerdict(
                tradingsymbol="INFY",
                company_name="Infosys",
                verdict="HOLD",
                confidence="MEDIUM",
                current_price=0.0,
                buy_price=0.0,
                pnl_pct=0.0,
                thesis_intact=True,
                bull_case="Stable franchise.",
                bear_case="IT slowdown risk.",
                what_to_watch="Deal wins",
                red_flags=[],
                rebalance_action="HOLD",
                rebalance_rupees=0.0,
                rebalance_reasoning="No action.",
                data_sources=["https://example.com"],
                analysis_duration_seconds=0.0,
                error=None,
            ),
            True,
        )

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
    snapshot = PortfolioSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
        total_value=10_000.0,
        available_cash=0.0,
        holdings=[make_holding("KPITTECH", 4.0, 8.0)],
    )
    sync_result = KiteSyncResult(
        profile={"user_name": "Saksham"},
        portfolio_snapshot=snapshot,
        portfolio_artifact=tmp_path / "portfolio.json",
        mf_snapshot=MFSnapshot(fetched_at="2026-03-18T10:00:00Z", total_value=0.0, holdings=[]),
        mf_artifact=tmp_path / "mf.json",
    )

    async def fake_sync_with_client(kite_client, *, settings=None, auto_login=True):
        return sync_result

    async def fake_kite_get_price_history(kite_client, tradingsymbol, instrument_token):
        return {"52w_high": 120.0, "52w_low": 80.0, "current_vs_52w_high_pct": -10.0}

    async def fake_get_company_artifact_and_verdict(**kwargs):
        return (
            object(),
            StockVerdict(
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
                red_flags=["promoter pledging"],
                rebalance_action="BUY",
                rebalance_rupees=0.0,
                rebalance_reasoning="",
                data_sources=[],
                analysis_duration_seconds=1.2,
                error=None,
            ),
            False,
        )

    monkeypatch.setattr(orchestrator, "sync_kite_data_with_client", fake_sync_with_client)
    monkeypatch.setattr(orchestrator, "build_kite_client", lambda settings: FakeKiteClient())
    monkeypatch.setattr(orchestrator, "kite_get_price_history", fake_kite_get_price_history)
    monkeypatch.setattr(orchestrator, "get_company_artifact_and_verdict", fake_get_company_artifact_and_verdict)
    monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: FakeSummaryClient())

    collected: list[orchestrator.RunEvent] = []
    await orchestrator.run_full_analysis(settings, event_callback=lambda e: collected.append(e))

    phase_events = [e for e in collected if e["type"] == "phase"]
    analyst_events = [e for e in collected if e["type"] == "analyst_complete"]

    # Phases arrive in correct order
    phase_names = [e["phase"] for e in phase_events]
    assert phase_names == ["kite_sync", "analyst", "rebalance", "summary"]

    # Analyst event carries rich verdict data
    assert len(analyst_events) == 1
    ev = analyst_events[0]
    assert ev["ticker"] == "KPITTECH"
    assert ev["verdict"] == "BUY"
    assert ev["confidence"] == "HIGH"
    assert ev["thesis_intact"] is True
    assert ev["red_flags"] == ["promoter pledging"]
    assert ev["completed"] == 1
    assert ev["total"] == 1

    # Analyst phase event has correct total
    analyst_phase = next(e for e in phase_events if e["phase"] == "analyst")
    assert analyst_phase["total"] == 1


def test_build_rebalance_only_report_excludes_passive_instruments() -> None:
    from models import PortfolioSnapshot

    snapshot = PortfolioSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
        total_value=10_000.0,
        available_cash=500.0,
        holdings=[
            make_holding("KPITTECH", 8.0, 8.0),
            make_holding("LIQUIDBEES", 5.0, 0.0),
        ],
    )
    report, actions = orchestrator.build_rebalance_only_report(snapshot)
    assert report.verdicts == []
    assert "rebalance-only" in report.portfolio_summary
    assert isinstance(report.total_buy_required, float)
    assert isinstance(report.total_sell_required, float)


def test_gate_helpers_cover_sell_and_missing_action() -> None:
    verdict = StockVerdict(
        tradingsymbol="BSE",
        company_name="BSE Ltd",
        verdict="SELL",
        confidence="MEDIUM",
        current_price=100.0,
        buy_price=90.0,
        pnl_pct=10.0,
        thesis_intact=True,
        bull_case="Good franchise.",
        bear_case="Valuation is rich.",
        what_to_watch="Volumes",
        red_flags=[],
        rebalance_action="BUY",
        rebalance_rupees=1000.0,
        rebalance_reasoning="Placeholder.",
        data_sources=["https://example.com"],
        analysis_duration_seconds=1.0,
        error=None,
    )
    merged = orchestrator._merge_action_into_verdict(verdict, None)
    assert merged.rebalance_action == "HOLD"
    assert merged.rebalance_reasoning == "No deterministic rebalance action was generated for this holding."
    assert orchestrator._should_gate_to_hold("SELL", True) is False
    assert orchestrator._should_gate_to_hold("STRONG_SELL", False) is False
    assert orchestrator._should_gate_to_hold("UNKNOWN", True) is True


def test_merge_action_into_verdict_rewrites_internal_hold_reasoning() -> None:
    verdict = StockVerdict(
        tradingsymbol="BSE",
        company_name="BSE Ltd",
        verdict="HOLD",
        confidence="HIGH",
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
        rebalance_reasoning="Placeholder.",
        data_sources=["https://example.com"],
        analysis_duration_seconds=1.0,
        error=None,
    )
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

    merged = orchestrator._merge_action_into_verdict(verdict, action)

    assert merged.rebalance_action == "HOLD"
    assert merged.rebalance_rupees == 0.0
    assert merged.rebalance_reasoning == (
        "Current conviction is unchanged. No rebalance action now; monitor drift versus target."
    )
    assert "Drift math suggested" not in merged.rebalance_reasoning
    assert "thesis_intact=" not in merged.rebalance_reasoning
    assert "deterministic sizing" not in merged.rebalance_reasoning
