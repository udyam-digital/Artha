from pathlib import Path

from config import Settings
from models import Holding, MFHolding, MFSnapshot, PortfolioSnapshot, ResearchDigest
from snapshot_store import (
    load_latest_mf_snapshot,
    load_latest_portfolio_snapshot,
    save_mf_snapshot,
    save_portfolio_snapshot,
    save_research_digest,
)


def _portfolio_snapshot() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
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


def _mf_snapshot() -> MFSnapshot:
    return MFSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
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


def test_save_and_load_latest_snapshots(tmp_path: Path) -> None:
    settings = Settings(ANTHROPIC_API_KEY="test-key", KITE_DATA_DIR=str(tmp_path))
    save_portfolio_snapshot(_portfolio_snapshot(), settings=settings)
    save_mf_snapshot(_mf_snapshot(), settings=settings)
    assert load_latest_portfolio_snapshot(settings).holdings[0].tradingsymbol == "HDFCBANK"
    assert load_latest_mf_snapshot(settings).holdings[0].fund == "Axis Midcap Fund"


def test_save_research_digest_writes_index_and_holdings(tmp_path: Path) -> None:
    settings = Settings(ANTHROPIC_API_KEY="test-key", REPORTS_DIR=str(tmp_path / "reports"))
    digest = ResearchDigest(
        generated_at="2026-03-18T10:00:00Z",
        portfolio_digest="Summary",
    )
    digest_path, holding_paths, index_path = save_research_digest(
        digest,
        {"HDFCBANK": {"identifier": "HDFCBANK"}},
        settings=settings,
    )
    assert digest_path.exists()
    assert index_path.exists()
    assert len(holding_paths) == 1
