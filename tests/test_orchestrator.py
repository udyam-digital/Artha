from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import orchestrator
from reliability import FullRunFailed, RetryFailure
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

    state = {"active": 0, "max_active": 0, "symbols": []}

    async def fake_get_company_artifact_and_verdict(**kwargs):
        holding = kwargs["holding"]
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

    progress = []
    report = await orchestrator.run_full_analysis(
        settings,
        progress_callback=lambda completed, total, verdict: progress.append((completed, total, verdict.tradingsymbol)),
    )

    assert set(state["symbols"]) == {"BSE", "KPITTECH"}
    assert state["max_active"] <= settings.analyst_parallelism
    assert len(report.verdicts) == 2
    assert [verdict.tradingsymbol for verdict in report.verdicts] == ["BSE", "KPITTECH"]
    assert report.verdicts[0].rebalance_action == "HOLD"
    assert report.verdicts[0].rebalance_rupees == 0.0
    assert report.verdicts[1].rebalance_action == "BUY"
    assert report.total_buy_required > 0
    assert report.total_sell_required == 0.0
    assert progress[-1][0] == 2
    assert fake_summary_client.calls[0].get("tools") is None
    assert fake_summary_client.calls[0]["model"] == "claude-sonnet-4-6"


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
    assert start_times[1] - start_times[0] >= 0.045
    assert start_times[2] - start_times[1] >= 0.045


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
        from tools import ToolExecutionError

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


async def test_run_single_company_analysis_returns_portfolio_report(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    snapshot = PortfolioSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
        total_value=10_000.0,
        available_cash=500.0,
        holdings=[make_holding("KPITTECH", 4.0, 8.0)],
    )

    monkeypatch.setattr(
        "snapshot_store.load_latest_portfolio_snapshot",
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
        "snapshot_store.load_latest_portfolio_snapshot",
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
    assert orchestrator._should_gate_to_hold("SELL", True) is True
    assert orchestrator._should_gate_to_hold("STRONG_SELL", False) is False
    assert orchestrator._should_gate_to_hold("UNKNOWN", True) is True
