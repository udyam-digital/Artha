from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import application.orchestrator as orchestrator
import application.run_helpers as run_helpers
from kite.runtime import KiteSyncResult
from models import MFSnapshot, PortfolioSnapshot, StockVerdict
from tests.orchestrator_support import FakeKiteClient, FakeSummaryClient, make_holding, make_settings

pytestmark = pytest.mark.anyio


def _verdict(
    symbol: str, *, verdict: str = "HOLD", action: str = "HOLD", reason: str = "No action.", error: str | None = None
) -> StockVerdict:
    return StockVerdict(
        tradingsymbol=symbol,
        company_name=symbol,
        verdict=verdict,
        confidence="MEDIUM" if verdict == "HOLD" else "HIGH",
        current_price=100.0,
        buy_price=90.0 if verdict == "HOLD" else 110.0,
        pnl_pct=10.0 if verdict == "HOLD" else -9.0,
        thesis_intact=True,
        bull_case="Good franchise.",
        bear_case="Valuation is rich.",
        what_to_watch="Volumes",
        red_flags=[] if error is None else ["error"],
        rebalance_action=action,
        rebalance_rupees=0.0,
        rebalance_reasoning=reason,
        data_sources=["https://example.com"],
        analysis_duration_seconds=1.0,
        error=error,
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
    sync_result = KiteSyncResult(
        profile={"user_name": "Saksham"},
        portfolio_snapshot=snapshot,
        portfolio_artifact=tmp_path / "portfolio.json",
        mf_snapshot=MFSnapshot(fetched_at="2026-03-18T10:00:00Z", total_value=0.0, holdings=[]),
        mf_artifact=tmp_path / "mf.json",
    )

    async def fake_sync_with_client(kite_client, settings=None, auto_login=True):
        return sync_result

    async def fake_price_contexts(**kwargs):
        return {
            symbol: {"52w_high": 120.0, "52w_low": 80.0, "current_vs_52w_high_pct": -10.0}
            for symbol in ["BSE", "KPITTECH"]
        }

    monkeypatch.setattr(orchestrator, "sync_kite_data_with_client", fake_sync_with_client)
    monkeypatch.setattr(orchestrator, "build_kite_client", lambda settings: FakeKiteClient())
    monkeypatch.setattr(orchestrator, "_price_contexts", fake_price_contexts)
    state = {"active": 0, "max_active": 0, "symbols": []}

    async def fake_get_company_artifact_and_verdict(**kwargs):
        holding = kwargs["holding"]
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        await asyncio.sleep(0)
        state["active"] -= 1
        state["symbols"].append(holding.tradingsymbol)
        return (
            object(),
            (
                _verdict("BSE")
                if holding.tradingsymbol == "BSE"
                else _verdict("KPITTECH", verdict="BUY", action="BUY", reason="Will be overwritten.")
            ),
            False,
        )

    monkeypatch.setattr(run_helpers, "get_company_artifact_and_verdict", fake_get_company_artifact_and_verdict)
    fake_summary_client = FakeSummaryClient()
    monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: fake_summary_client)
    events = []
    report = await orchestrator.run_full_analysis(settings, event_callback=lambda event: events.append(event))
    assert set(state["symbols"]) == {"BSE", "KPITTECH"}
    assert state["max_active"] <= settings.analyst_parallelism
    assert [verdict.tradingsymbol for verdict in report.verdicts] == ["BSE", "KPITTECH"]
    assert (
        report.verdicts[0].rebalance_reasoning
        == "Current conviction is unchanged. No rebalance action now; monitor drift versus target."
    )
    assert report.verdicts[1].rebalance_action == "BUY"
    assert fake_summary_client.calls[0]["model"] == "claude-sonnet-4-6"
    assert [e["phase"] for e in events if e["type"] == "phase"] == ["kite_sync", "analyst", "rebalance", "summary"]


async def test_run_full_analysis_degrades_when_macro_context_fails(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    sync_result = KiteSyncResult(
        profile={"user_name": "Saksham"},
        portfolio_snapshot=PortfolioSnapshot(
            fetched_at="2026-03-18T10:00:00Z",
            total_value=10_000.0,
            available_cash=0.0,
            holdings=[make_holding("BSE", 8.0, 8.0)],
        ),
        portfolio_artifact=tmp_path / "portfolio.json",
        mf_snapshot=MFSnapshot(fetched_at="2026-03-18T10:00:00Z", total_value=0.0, holdings=[]),
        mf_artifact=tmp_path / "mf.json",
    )

    async def fake_sync_with_client(kite_client, settings=None, auto_login=True):
        return sync_result

    async def fake_price_contexts(**kwargs):
        return {"BSE": {"52w_high": 120.0, "52w_low": 80.0, "current_vs_52w_high_pct": -10.0}}

    monkeypatch.setattr(orchestrator, "sync_kite_data_with_client", fake_sync_with_client)
    monkeypatch.setattr(orchestrator, "build_kite_client", lambda settings: FakeKiteClient())
    monkeypatch.setattr(orchestrator, "_price_contexts", fake_price_contexts)

    async def failing_macro_summary():
        return "", ["cpi: unavailable", "iip: unavailable", "gdp: unavailable"]

    monkeypatch.setattr(orchestrator, "_build_macro_summary", failing_macro_summary)

    async def fake_get_company_artifact_and_verdict(**kwargs):
        return object(), _verdict("BSE"), False

    monkeypatch.setattr(run_helpers, "get_company_artifact_and_verdict", fake_get_company_artifact_and_verdict)
    monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: FakeSummaryClient())
    report = await orchestrator.run_full_analysis(settings)
    assert report.errors == ["cpi: unavailable", "iip: unavailable", "gdp: unavailable"]


async def test_run_full_analysis_reuses_fresh_company_cache(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    sync_result = KiteSyncResult(
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
    calls = {"count": 0}

    async def fake_sync_with_client(kite_client, settings=None, auto_login=True):
        return sync_result

    async def fake_price_contexts(**kwargs):
        return {"KPITTECH": {"52w_high": 120.0, "52w_low": 80.0, "current_vs_52w_high_pct": -10.0}}

    monkeypatch.setattr(orchestrator, "sync_kite_data_with_client", fake_sync_with_client)
    monkeypatch.setattr(orchestrator, "build_kite_client", lambda settings: FakeKiteClient())
    monkeypatch.setattr(orchestrator, "_price_contexts", fake_price_contexts)

    async def fake_get_company_artifact_and_verdict(**kwargs):
        calls["count"] += 1
        return object(), _verdict("KPITTECH", verdict="BUY", action="BUY"), True

    monkeypatch.setattr(run_helpers, "get_company_artifact_and_verdict", fake_get_company_artifact_and_verdict)
    monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: FakeSummaryClient())
    report = await orchestrator.run_full_analysis(settings)
    assert calls["count"] == 1
    assert report.verdicts[0].analysis_duration_seconds == 0.0


async def test_run_full_analysis_parallelism_and_staggering(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path, ANALYST_PARALLELISM=2, ANALYST_MIN_START_INTERVAL_SECONDS=0.04)
    sync_result = KiteSyncResult(
        profile={"user_name": "Saksham"},
        portfolio_snapshot=PortfolioSnapshot(
            fetched_at="2026-03-18T10:00:00Z",
            total_value=10_000.0,
            available_cash=0.0,
            holdings=[
                make_holding("BSE", 14.0, 8.0),
                make_holding("KPITTECH", 4.0, 8.0),
                make_holding("HDFCBANK", 7.0, 8.0),
            ],
        ),
        portfolio_artifact=tmp_path / "portfolio.json",
        mf_snapshot=MFSnapshot(fetched_at="2026-03-18T10:00:00Z", total_value=0.0, holdings=[]),
        mf_artifact=tmp_path / "mf.json",
    )

    async def fake_sync_with_client(kite_client, settings=None, auto_login=True):
        return sync_result

    async def fake_price_contexts(**kwargs):
        return {
            holding.tradingsymbol: {"52w_high": 120.0, "52w_low": 80.0, "current_vs_52w_high_pct": -10.0}
            for holding in kwargs["holdings"]
        }

    monkeypatch.setattr(orchestrator, "sync_kite_data_with_client", fake_sync_with_client)
    monkeypatch.setattr(orchestrator, "build_kite_client", lambda settings: FakeKiteClient())
    monkeypatch.setattr(orchestrator, "_price_contexts", fake_price_contexts)
    state = {"active": 0, "max_active": 0, "starts": []}

    async def fake_get_company_artifact_and_verdict(**kwargs):
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        state["starts"].append(asyncio.get_running_loop().time())
        await asyncio.sleep(0)
        state["active"] -= 1
        return object(), _verdict(kwargs["holding"].tradingsymbol), False

    monkeypatch.setattr(run_helpers, "get_company_artifact_and_verdict", fake_get_company_artifact_and_verdict)
    monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: FakeSummaryClient())
    await orchestrator.run_full_analysis(settings)
    assert state["max_active"] <= 2
    assert state["starts"][1] - state["starts"][0] >= 0.03


async def test_build_portfolio_summary_and_budget(tmp_path: Path, monkeypatch, caplog) -> None:
    settings = make_settings(tmp_path)
    client = FakeSummaryClient()
    monkeypatch.setattr(
        client, "messages_count_tokens", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("count failed"))
    )
    with caplog.at_level("WARNING"):
        summary = await orchestrator._build_portfolio_summary(
            client=client,
            settings=settings,
            verdicts=[],
            snapshot=PortfolioSnapshot(
                fetched_at="2026-03-18T10:00:00Z", total_value=1_000.0, available_cash=0.0, holdings=[]
            ),
            mf_symbols=[],
            errors=[],
        )
    assert summary == "Portfolio summary"
    assert "exact token counting failed" in caplog.text


async def test_run_full_analysis_uses_token_budget_manager(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    sync_result = KiteSyncResult(
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
    budget_calls = []

    class FakeBudgetManager:
        def __init__(self, *args, **kwargs):
            pass

        async def acquire(self, *, estimated_input_tokens: int, estimated_output_tokens: int) -> None:
            budget_calls.append((estimated_input_tokens, estimated_output_tokens))

        def record_actual(self, *, input_tokens: int, output_tokens: int) -> None:
            return None

    monkeypatch.setattr(orchestrator, "TokenBudgetManager", FakeBudgetManager)

    async def fake_sync_with_client(kite_client, settings=None, auto_login=True):
        return sync_result

    async def fake_price_contexts(**kwargs):
        return {"KPITTECH": {"52w_high": 120.0, "52w_low": 80.0, "current_vs_52w_high_pct": -10.0}}

    monkeypatch.setattr(orchestrator, "sync_kite_data_with_client", fake_sync_with_client)
    monkeypatch.setattr(orchestrator, "build_kite_client", lambda settings: FakeKiteClient())
    monkeypatch.setattr(orchestrator, "_price_contexts", fake_price_contexts)

    async def fake_get_company_artifact_and_verdict(**kwargs):
        return object(), _verdict("KPITTECH"), False

    monkeypatch.setattr(run_helpers, "get_company_artifact_and_verdict", fake_get_company_artifact_and_verdict)
    monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: FakeSummaryClient())
    await orchestrator.run_full_analysis(settings)
    assert budget_calls == [(4000, 1500)]
