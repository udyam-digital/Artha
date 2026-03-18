import asyncio
from pathlib import Path
from types import SimpleNamespace

from config import Settings
from models import Holding, MFHolding, MFSnapshot, PortfolioSnapshot
from research import DeepResearchOrchestrator


class FakeAnthropicClient:
    def __init__(self) -> None:
        self.calls = []

    async def messages_create(self, **kwargs):
        self.calls.append(kwargs)
        prompt = kwargs["messages"][0]["content"]
        if "mutual fund" in prompt:
            text = (
                '<mf_research>{"identifier":"AXISMIDCAP","title":"Axis Midcap Fund",'
                '"data_freshness":"Latest factsheet reviewed","sources":["https://example.com/mf"],'
                '"fund_house":"Axis","category":"Mid Cap","mandate":"Mid-cap equity",'
                '"portfolio_style":"Growth","expense_ratio_note":"Competitive direct-plan expense ratio",'
                '"aum_note":"Healthy AUM","overlap_risk":"Moderate overlap with diversified equity funds",'
                '"recent_commentary":"Positioning remains growth-oriented","risks":["Mid-cap volatility"],'
                '"confidence_summary":"Enough current data collected."}</mf_research>'
            )
        elif "Input JSON" in prompt:
            text = "Portfolio digest"
        else:
            text = (
                '<equity_research>{"identifier":"HDFCBANK","title":"HDFC Bank",'
                '"data_freshness":"Q3 FY26 results available","sources":["https://example.com/equity"],'
                '"bull_case":"Strong franchise","bear_case":"Margin pressure",'
                '"what_to_watch":"Loan growth","red_flags":[],"confidence_summary":"Enough current data collected."}'
                "</equity_research>"
            )
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])

    @property
    def messages(self):
        return SimpleNamespace(create=self.messages_create)


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        ANTHROPIC_API_KEY="test-key",
        MODEL="claude-sonnet-4-6",
        ANALYST_MODEL="claude-haiku-4-5",
        REPORTS_DIR=str(tmp_path / "reports"),
        KITE_DATA_DIR=str(tmp_path / "kite"),
    )


def test_research_orchestrator_saves_equity_and_mf_reports(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    orchestrator = DeepResearchOrchestrator(settings=settings, client=FakeAnthropicClient())  # type: ignore[arg-type]
    portfolio_snapshot = PortfolioSnapshot(
        fetched_at="2026-03-18T10:00:00Z",
        total_value=1000.0,
        available_cash=0.0,
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
    digest, digest_path, holding_paths, index_path = asyncio.run(
        orchestrator.research_snapshots(
            portfolio_snapshot,
            mf_snapshot,
        )
    )
    assert len(digest.equity_reports) == 1
    assert len(digest.mf_reports) == 1
    assert digest_path.exists()
    assert index_path.exists()
    assert len(holding_paths) == 2
