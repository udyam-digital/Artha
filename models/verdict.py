from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field

from models.analyst import AnalystReportCard
from models.portfolio import PortfolioSnapshot, StrictModel


class CompanyAnalysisArtifact(StrictModel):
    generated_at: datetime
    source_model: str
    exchange: str
    ticker: str
    report_card: AnalystReportCard
    yfinance_data: dict[str, object] = Field(default_factory=dict)


class Verdict(str, Enum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"


class StockVerdict(StrictModel):
    tradingsymbol: str
    company_name: str
    verdict: Verdict
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    current_price: float
    buy_price: float
    pnl_pct: float
    thesis_intact: bool
    bull_case: str
    bear_case: str
    what_to_watch: str
    red_flags: list[str] = Field(default_factory=list)
    rebalance_action: Literal["BUY", "SELL", "HOLD"]
    rebalance_rupees: float
    rebalance_reasoning: str
    data_sources: list[str] = Field(default_factory=list)
    yfinance_data: dict[str, object] = Field(default_factory=dict)
    analysis_duration_seconds: float
    error: str | None = None


class RebalancingAction(StrictModel):
    tradingsymbol: str
    action: Literal["BUY", "SELL", "HOLD"]
    current_weight_pct: float
    target_weight_pct: float
    drift_pct: float
    rupee_amount: float
    quantity_approx: int
    reasoning: str
    urgency: Literal["HIGH", "MEDIUM", "LOW"]


class PortfolioReport(StrictModel):
    generated_at: datetime
    portfolio_snapshot: PortfolioSnapshot
    verdicts: list[StockVerdict] = Field(default_factory=list)
    portfolio_summary: str
    total_buy_required: float
    total_sell_required: float
    errors: list[str] = Field(default_factory=list)
