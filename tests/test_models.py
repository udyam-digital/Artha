from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from models import AnalystRiskMatrix, Holding, MacroContext, PortfolioReport, PortfolioSnapshot, StockVerdict


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
    verdict = StockVerdict(
        tradingsymbol="HDFCBANK",
        company_name="HDFC Bank",
        verdict="HOLD",
        confidence="MEDIUM",
        current_price=120.0,
        buy_price=100.0,
        pnl_pct=20.0,
        thesis_intact=True,
        bull_case="Strong franchise.",
        bear_case="Margin pressure.",
        what_to_watch="Loan growth",
        red_flags=[],
        rebalance_action="HOLD",
        rebalance_rupees=0.0,
        rebalance_reasoning="Sizing is appropriate.",
        data_sources=["https://example.com"],
        yfinance_data={"ticker": "HDFCBANK.NS", "cmp": 120.0},
        analysis_duration_seconds=2.5,
        error=None,
    )
    report = PortfolioReport(
        generated_at=datetime.now(UTC),
        portfolio_snapshot=snapshot,
        verdicts=[verdict],
        portfolio_summary="Summary",
        total_buy_required=0.0,
        total_sell_required=0.0,
        errors=[],
    )
    restored = PortfolioReport.model_validate_json(report.model_dump_json())
    assert restored == report


def test_legacy_report_fields_are_rejected() -> None:
    snapshot = PortfolioSnapshot(
        fetched_at=datetime.now(UTC),
        total_value=1000.0,
        available_cash=0.0,
        holdings=[],
    )
    with pytest.raises(ValidationError):
        PortfolioReport(
            generated_at=datetime.now(UTC),
            portfolio_snapshot=snapshot,
            verdicts=[],
            analyses=[],
            portfolio_summary="Summary",
            total_buy_required=0.0,
            total_sell_required=0.0,
            errors=[],
        )


def test_risk_level_normalizes_extended_values() -> None:
    assert AnalystRiskMatrix(risk_level="Medium-High").risk_level == "High"
    assert AnalystRiskMatrix(risk_level="Medium-Low").risk_level == "Low"


def test_risk_level_normalizes_simple_case_variants() -> None:
    assert AnalystRiskMatrix(risk_level=" high ").risk_level == "High"
    assert AnalystRiskMatrix(risk_level="MEDIUM").risk_level == "Medium"
    assert AnalystRiskMatrix(risk_level="low").risk_level == "Low"


def test_macro_context_serialization() -> None:
    payload = MacroContext(
        cpi_headline_yoy=4.5,
        iip_growth_latest=3.2,
        gdp_growth_latest=6.4,
        as_of_date="2026-03",
        fetch_errors=["iip: partial"],
    )
    restored = MacroContext.model_validate_json(payload.model_dump_json())
    assert restored == payload


def test_stock_verdict_yfinance_data_defaults_to_empty_dict() -> None:
    verdict = StockVerdict(
        tradingsymbol="HDFCBANK",
        company_name="HDFC Bank",
        verdict="HOLD",
        confidence="MEDIUM",
        current_price=120.0,
        buy_price=100.0,
        pnl_pct=20.0,
        thesis_intact=True,
        bull_case="Strong franchise.",
        bear_case="Margin pressure.",
        what_to_watch="Loan growth",
        red_flags=[],
        rebalance_action="HOLD",
        rebalance_rupees=0.0,
        rebalance_reasoning="Sizing is appropriate.",
        data_sources=["https://example.com"],
        analysis_duration_seconds=2.5,
        error=None,
    )
    assert verdict.yfinance_data == {}
