from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from models import Holding, PortfolioReport, PortfolioSnapshot


def test_holding_validation() -> None:
    with pytest.raises(ValidationError):
        Holding(tradingsymbol="HDFCBANK")


def test_negative_pnl() -> None:
    holding = Holding(
        tradingsymbol="HDFCBANK",
        exchange="NSE",
        quantity=10,
        average_price=100.0,
        last_price=90.0,
        current_value=900.0,
        current_weight_pct=10.0,
        target_weight_pct=8.0,
        pnl=-100.0,
        pnl_pct=-10.0,
        instrument_token=123,
    )
    assert holding.pnl < 0


def test_portfolio_report_serialization() -> None:
    snapshot = PortfolioSnapshot(
        fetched_at=datetime.now(UTC),
        total_value=1000.0,
        available_cash=100.0,
        holdings=[],
    )
    report = PortfolioReport(
        generated_at=datetime.now(UTC),
        portfolio_snapshot=snapshot,
        analyses=[],
        rebalancing_actions=[],
        portfolio_summary="Summary",
        total_buy_required=0.0,
        total_sell_required=0.0,
        errors=[],
    )
    restored = PortfolioReport.model_validate_json(report.model_dump_json())
    assert restored == report
