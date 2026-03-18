from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Holding(StrictModel):
    tradingsymbol: str
    exchange: str
    quantity: int
    average_price: float
    last_price: float
    current_value: float
    current_weight_pct: float
    target_weight_pct: float
    pnl: float
    pnl_pct: float
    instrument_token: int


class PortfolioSnapshot(StrictModel):
    fetched_at: datetime
    total_value: float
    available_cash: float
    holdings: list[Holding]


class MFHolding(StrictModel):
    tradingsymbol: str
    fund: str
    folio: str
    quantity: float
    average_price: float
    last_price: float
    current_value: float
    pnl: float
    pnl_pct: float
    scheme_type: str
    plan: str


class MFSnapshot(StrictModel):
    fetched_at: datetime
    total_value: float
    holdings: list[MFHolding]


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


class ResearchArtifact(StrictModel):
    generated_at: datetime
    holding_type: Literal["EQUITY", "MF"]
    identifier: str
    title: str
    data_freshness: str
    sources: list[str] = Field(default_factory=list)


class EquityResearchArtifact(ResearchArtifact):
    holding_type: Literal["EQUITY"] = "EQUITY"
    bull_case: str
    bear_case: str
    what_to_watch: str
    red_flags: list[str] = Field(default_factory=list)
    confidence_summary: str


class MFResearchArtifact(ResearchArtifact):
    holding_type: Literal["MF"] = "MF"
    fund_house: str
    category: str
    mandate: str
    portfolio_style: str
    expense_ratio_note: str
    aum_note: str
    overlap_risk: str
    recent_commentary: str
    risks: list[str] = Field(default_factory=list)
    confidence_summary: str


class ResearchDigest(StrictModel):
    generated_at: datetime
    equity_reports: list[EquityResearchArtifact] = Field(default_factory=list)
    mf_reports: list[MFResearchArtifact] = Field(default_factory=list)
    portfolio_digest: str
    errors: list[str] = Field(default_factory=list)
