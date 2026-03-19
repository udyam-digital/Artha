from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import Settings
from models import MFHolding, MFSnapshot, Holding, PortfolioSnapshot
from snapshot_store import save_mf_snapshot, save_portfolio_snapshot
from kite_runtime import load_same_day_kite_sync_result


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        ANTHROPIC_API_KEY="test-key",
        REPORTS_DIR=str(tmp_path / "reports"),
        KITE_DATA_DIR=str(tmp_path / "kite"),
    )


def make_portfolio_snapshot(fetched_at: datetime) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        fetched_at=fetched_at,
        total_value=1000.0,
        available_cash=100.0,
        holdings=[
            Holding(
                tradingsymbol="HDFCBANK",
                exchange="NSE",
                quantity=1,
                average_price=100.0,
                last_price=120.0,
                current_value=120.0,
                current_weight_pct=12.0,
                target_weight_pct=10.0,
                pnl=20.0,
                pnl_pct=20.0,
                instrument_token=1,
            )
        ],
    )


def make_mf_snapshot(fetched_at: datetime) -> MFSnapshot:
    return MFSnapshot(
        fetched_at=fetched_at,
        total_value=500.0,
        holdings=[
            MFHolding(
                tradingsymbol="MF1",
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


def test_load_same_day_kite_sync_result_returns_cached_snapshots(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    fetched_at = datetime.now(timezone.utc)
    save_portfolio_snapshot(make_portfolio_snapshot(fetched_at), settings=settings)
    save_mf_snapshot(make_mf_snapshot(fetched_at), settings=settings)

    result = load_same_day_kite_sync_result(settings)

    assert result is not None
    assert result.portfolio_snapshot.holdings[0].tradingsymbol == "HDFCBANK"
    assert result.mf_snapshot.holdings[0].fund == "Axis Midcap Fund"


def test_load_same_day_kite_sync_result_returns_none_for_stale_snapshots(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    fetched_at = datetime.now(timezone.utc) - timedelta(days=1)
    save_portfolio_snapshot(make_portfolio_snapshot(fetched_at), settings=settings)
    save_mf_snapshot(make_mf_snapshot(fetched_at), settings=settings)

    assert load_same_day_kite_sync_result(settings) is None
