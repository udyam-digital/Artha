from pathlib import Path

import json

from config import Settings
from models import CompanyAnalysisArtifact, Holding, MFHolding, MFSnapshot, PortfolioSnapshot, ResearchDigest
from persistence.store import (
    load_company_analysis_artifact,
    load_latest_mf_snapshot,
    load_latest_portfolio_snapshot,
    save_company_analysis_artifact,
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


def _company_artifact() -> CompanyAnalysisArtifact:
    return CompanyAnalysisArtifact.model_validate(
        {
            "generated_at": "2026-03-18T10:00:00Z",
            "source_model": "claude-haiku-4-5",
            "exchange": "NSE",
            "ticker": "KPITTECH",
            "report_card": {
                "stock_snapshot": {
                    "name": "KPIT Technologies",
                    "ticker": "KPITTECH",
                    "sector": "Technology",
                    "market_cap_category": "Mid",
                    "52w_high": 1928.0,
                    "52w_low": 980.0,
                    "current_price": 1420.0,
                    "time_horizon": "Compounder",
                },
                "thesis": {
                    "core_idea": "Engineering-led software franchise.",
                    "growth_driver": "Auto software demand remains healthy.",
                    "edge": "Deep domain capability.",
                    "trigger": "Large deal pipeline conversion",
                },
                "growth_engine": {
                    "revenue_cagr": "24%",
                    "eps_cagr": "22%",
                    "sector_tailwind": "High",
                    "growth_score": 8,
                },
                "quality": {
                    "roce": "28%",
                    "roe": "24%",
                    "debt_to_equity": "0.02",
                    "fcf_status": "Positive",
                    "governance_flags": "None identified",
                    "quality_score": 8,
                },
                "valuation": {
                    "pe": "52x",
                    "sector_pe": "48x",
                    "peg": "2.1",
                    "fcf_yield": "1.8%",
                    "fair_value_range": [1300, 1500],
                    "margin_of_safety": "Limited",
                    "rvs_score": 6,
                },
                "timing": {
                    "price_vs_200dma": "+6%",
                    "momentum": "Neutral",
                    "fii_trend": "Stable",
                    "timing_signal": "Neutral",
                },
                "capital_efficiency": {
                    "roic_trend": "Improving",
                    "reinvestment_quality": "Disciplined",
                    "capital_efficiency_score": 8,
                },
                "risk_matrix": {
                    "structural_risks": ["Auto program delays"],
                    "cyclical_risks": ["Global auto slowdown"],
                    "company_risks": ["Execution slippage"],
                    "risk_level": "Medium",
                },
                "action_plan": {
                    "buy_zone": [1250, 1350],
                    "add_zone": 1380,
                    "hold_zone": "1350-1550",
                    "trim_zone": 1650,
                    "stop_loss": 1180,
                },
                "position_sizing": {
                    "suggested_allocation": "5-6%",
                    "max_allocation": "8%",
                },
                "final_verdict": {
                    "verdict": "ADD",
                    "confidence": "High",
                },
                "monitoring": {
                    "next_triggers": ["Quarterly margin trajectory"],
                    "key_metrics": ["Large-deal wins"],
                    "red_flags": ["Client concentration rise"],
                },
                "data_sources": [
                    "https://www.screener.in/company/KPITTECH/",
                    "https://www.example.com/kpit-results",
                ],
            },
        }
    )


def test_save_and_load_company_analysis_artifact_uses_alias_keys(tmp_path: Path) -> None:
    settings = Settings(ANTHROPIC_API_KEY="test-key", KITE_DATA_DIR=str(tmp_path / "kite"))
    path = save_company_analysis_artifact(_company_artifact(), settings=settings)
    payload = json.loads(path.read_text(encoding="utf-8"))
    stock_snapshot = payload["report_card"]["stock_snapshot"]

    assert "52w_high" in stock_snapshot
    assert "52w_low" in stock_snapshot
    assert "high_52w" not in stock_snapshot
    assert "low_52w" not in stock_snapshot

    loaded = load_company_analysis_artifact("KPITTECH", settings=settings)
    assert loaded.ticker == "KPITTECH"
    assert loaded.report_card.stock_snapshot.high_52w == 1928.0
    assert loaded.report_card.stock_snapshot.low_52w == 980.0


def test_load_company_analysis_artifact_migrates_legacy_52w_fields(tmp_path: Path) -> None:
    settings = Settings(ANTHROPIC_API_KEY="test-key", KITE_DATA_DIR=str(tmp_path / "kite"))
    path = tmp_path / "companies" / "KPITTECH.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _company_artifact().model_dump(mode="json")
    stock_snapshot = payload["report_card"]["stock_snapshot"]
    stock_snapshot["high_52w"] = stock_snapshot.pop("high_52w")
    stock_snapshot["low_52w"] = stock_snapshot.pop("low_52w")
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    loaded = load_company_analysis_artifact("KPITTECH", settings=settings)
    migrated_payload = json.loads(path.read_text(encoding="utf-8"))
    migrated_stock_snapshot = migrated_payload["report_card"]["stock_snapshot"]

    assert loaded.report_card.stock_snapshot.high_52w == 1928.0
    assert loaded.report_card.stock_snapshot.low_52w == 980.0
    assert "52w_high" in migrated_stock_snapshot
    assert "52w_low" in migrated_stock_snapshot
    assert "high_52w" not in migrated_stock_snapshot
    assert "low_52w" not in migrated_stock_snapshot
