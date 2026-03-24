from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import application.orchestrator as orchestrator
import application.run_helpers as run_helpers
from kite.runtime import KiteSyncResult
from models import MFSnapshot, PortfolioSnapshot
from reliability import FullRunFailed, RetryFailure
from tests.orchestrator_support import FakeKiteClient, FakeSummaryClient, make_holding, make_settings
from tests.test_orchestrator_flow import _verdict

pytestmark = pytest.mark.anyio


async def test_run_full_analysis_continues_when_price_history_is_missing(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    sync_result = KiteSyncResult(
        profile={"user_name": "Saksham"},
        portfolio_snapshot=PortfolioSnapshot(
            fetched_at="2026-03-18T10:00:00Z",
            total_value=10_000.0,
            available_cash=0.0,
            holdings=[make_holding("TIPSMUSIC", 4.0, 8.0)],
        ),
        portfolio_artifact=tmp_path / "portfolio.json",
        mf_snapshot=MFSnapshot(fetched_at="2026-03-18T10:00:00Z", total_value=0.0, holdings=[]),
        mf_artifact=tmp_path / "mf.json",
    )
    captured = []
    monkeypatch.setattr(orchestrator, "build_kite_client", lambda settings: FakeKiteClient())

    async def fake_sync_with_client(kite_client, settings=None, auto_login=True):
        return sync_result

    async def missing_price_contexts(**kwargs):
        return {"TIPSMUSIC": orchestrator._default_price_context()}

    monkeypatch.setattr(orchestrator, "sync_kite_data_with_client", fake_sync_with_client)
    monkeypatch.setattr(orchestrator, "_price_contexts", missing_price_contexts)

    async def fake_get_company_artifact_and_verdict(**kwargs):
        captured.append(kwargs["price_context"])
        return object(), _verdict("TIPSMUSIC"), False

    monkeypatch.setattr(run_helpers, "get_company_artifact_and_verdict", fake_get_company_artifact_and_verdict)
    monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: FakeSummaryClient())
    report = await orchestrator.run_full_analysis(settings)
    assert report.verdicts[0].tradingsymbol == "TIPSMUSIC"
    assert captured[0]["52w_high"] == 0.0


@pytest.mark.parametrize("phase", ["kite_sync", "price_history", "portfolio_summary"])
async def test_run_full_analysis_wraps_retry_failures(tmp_path: Path, monkeypatch, phase: str) -> None:
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
    if phase != "kite_sync":

        async def fake_sync_with_client(kite_client, settings=None, auto_login=True):
            return sync_result

        async def fake_price_contexts(**kwargs):
            return {"KPITTECH": {"52w_high": 120.0, "52w_low": 80.0, "current_vs_52w_high_pct": -10.0}}

        async def fake_get_company_artifact_and_verdict(**kwargs):
            return object(), _verdict("KPITTECH", verdict="BUY", action="BUY"), False

        monkeypatch.setattr(orchestrator, "build_kite_client", lambda settings: FakeKiteClient())
        monkeypatch.setattr(orchestrator, "sync_kite_data_with_client", fake_sync_with_client)
        monkeypatch.setattr(orchestrator, "_price_contexts", fake_price_contexts)
        monkeypatch.setattr(run_helpers, "get_company_artifact_and_verdict", fake_get_company_artifact_and_verdict)
        monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: FakeSummaryClient())
    if phase == "price_history":

        async def failing_price_contexts(**kwargs):
            raise RetryFailure(
                phase="price_history", cause=TimeoutError("price timeout"), retries_used=1, ticker="KPITTECH"
            )

        monkeypatch.setattr(orchestrator, "_price_contexts", failing_price_contexts)
    target_phase = phase

    async def fake_run_with_retries(func, *, phase: str, **kwargs):
        if phase == target_phase:
            raise RetryFailure(
                phase=phase,
                cause=TimeoutError(f"{phase} timeout"),
                retries_used=2,
                ticker="KPITTECH" if phase == "price_history" else None,
            )
        return await func()

    monkeypatch.setattr(orchestrator, "run_with_retries", fake_run_with_retries)
    with pytest.raises(FullRunFailed) as exc_info:
        await orchestrator.run_full_analysis(settings)
    assert exc_info.value.phase == phase


async def test_run_full_analysis_fails_fast_and_cancels(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path, ANALYST_PARALLELISM=2)
    sync_result = KiteSyncResult(
        profile={"user_name": "Saksham"},
        portfolio_snapshot=PortfolioSnapshot(
            fetched_at="2026-03-18T10:00:00Z",
            total_value=10_000.0,
            available_cash=0.0,
            holdings=[make_holding("KPITTECH", 4.0, 8.0), make_holding("BSE", 14.0, 8.0)],
        ),
        portfolio_artifact=tmp_path / "portfolio.json",
        mf_snapshot=MFSnapshot(fetched_at="2026-03-18T10:00:00Z", total_value=0.0, holdings=[]),
        mf_artifact=tmp_path / "mf.json",
    )
    cancelled = asyncio.Event()

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

    async def fake_get_company_artifact_and_verdict(**kwargs):
        if kwargs["holding"].tradingsymbol == "KPITTECH":
            raise TimeoutError("anthropic timeout")
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(run_helpers, "get_company_artifact_and_verdict", fake_get_company_artifact_and_verdict)
    monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: FakeSummaryClient())
    with pytest.raises(FullRunFailed):
        await orchestrator.run_full_analysis(settings)
    assert cancelled.is_set()


async def test_run_full_analysis_fails_on_verdict_error_payload(tmp_path: Path, monkeypatch) -> None:
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

    async def fake_sync_with_client(kite_client, settings=None, auto_login=True):
        return sync_result

    async def fake_price_contexts(**kwargs):
        return {"KPITTECH": {"52w_high": 120.0, "52w_low": 80.0, "current_vs_52w_high_pct": -10.0}}

    monkeypatch.setattr(orchestrator, "sync_kite_data_with_client", fake_sync_with_client)
    monkeypatch.setattr(orchestrator, "build_kite_client", lambda settings: FakeKiteClient())
    monkeypatch.setattr(orchestrator, "_price_contexts", fake_price_contexts)

    async def fake_get_company_artifact_and_verdict(**kwargs):
        return object(), _verdict("KPITTECH", error="analyst returned fallback"), False

    monkeypatch.setattr(run_helpers, "get_company_artifact_and_verdict", fake_get_company_artifact_and_verdict)
    monkeypatch.setattr(orchestrator, "AsyncAnthropic", lambda api_key: FakeSummaryClient())
    with pytest.raises(FullRunFailed):
        await orchestrator.run_full_analysis(settings)
