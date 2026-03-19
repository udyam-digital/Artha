from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


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


class AnalystStockSnapshot(StrictModel):
    name: str
    ticker: str
    sector: str
    market_cap_category: Literal["Large", "Mid", "Small"]
    current_price: float
    high_52w: float = Field(alias="52w_high")
    low_52w: float = Field(alias="52w_low")
    time_horizon: Literal["Compounder", "Cyclical", "Tactical"]


class AnalystThesis(StrictModel):
    core_idea: str
    growth_driver: str
    edge: str
    trigger: str


class AnalystGrowthEngine(StrictModel):
    revenue_cagr: str
    eps_cagr: str
    sector_tailwind: Literal["High", "Medium", "Low"]
    growth_score: int


class AnalystQuality(StrictModel):
    roce: str
    roe: str
    debt_to_equity: str
    fcf_status: Literal["Positive", "Negative"]
    governance_flags: str
    quality_score: int


class AnalystValuation(StrictModel):
    pe: str
    sector_pe: str
    peg: str
    fcf_yield: str
    fair_value_range: list[float]
    margin_of_safety: str
    rvs_score: int


class AnalystTiming(StrictModel):
    price_vs_200dma: str
    momentum: Literal["Bullish", "Neutral", "Bearish"]
    fii_trend: str
    timing_signal: Literal["Favorable", "Neutral", "Risky"]


class AnalystCapitalEfficiency(StrictModel):
    roic_trend: str
    reinvestment_quality: str
    capital_efficiency_score: int


class AnalystRiskMatrix(StrictModel):
    structural_risks: list[str] = Field(default_factory=list)
    cyclical_risks: list[str] = Field(default_factory=list)
    company_risks: list[str] = Field(default_factory=list)
    risk_level: Literal["Low", "Medium", "High"]

    @field_validator("risk_level", mode="before")
    @classmethod
    def normalize_risk_level(cls, value: object) -> str:
        normalized = str(value).strip()
        mapping = {
            "Medium-High": "High",
            "medium-high": "High",
            "MEDIUM-HIGH": "High",
            "Medium-Low": "Low",
            "medium-low": "Low",
            "MEDIUM-LOW": "Low",
        }
        return mapping.get(normalized, normalized)


class AnalystActionPlan(StrictModel):
    buy_zone: list[float]
    add_zone: float
    hold_zone: str
    trim_zone: float
    stop_loss: float


class AnalystPositionSizing(StrictModel):
    suggested_allocation: str
    max_allocation: str


class AnalystFinalVerdict(StrictModel):
    verdict: Literal["BUY", "ADD", "HOLD", "TRIM", "EXIT"]
    confidence: Literal["High", "Medium", "Low", "HIGH", "MEDIUM", "LOW"]


class AnalystMonitoring(StrictModel):
    next_triggers: list[str] = Field(default_factory=list)
    key_metrics: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)


class AnalystReportCard(StrictModel):
    stock_snapshot: AnalystStockSnapshot
    thesis: AnalystThesis
    growth_engine: AnalystGrowthEngine
    quality: AnalystQuality
    valuation: AnalystValuation
    timing: AnalystTiming
    capital_efficiency: AnalystCapitalEfficiency
    risk_matrix: AnalystRiskMatrix
    action_plan: AnalystActionPlan
    position_sizing: AnalystPositionSizing
    final_verdict: AnalystFinalVerdict
    monitoring: AnalystMonitoring
    data_sources: list[str] = Field(default_factory=list)


class CompanyAnalysisArtifact(StrictModel):
    generated_at: datetime
    source_model: str
    exchange: str
    ticker: str
    report_card: AnalystReportCard


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
