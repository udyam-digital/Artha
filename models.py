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


class MacroContext(StrictModel):
    cpi_headline_yoy: float | None = None
    iip_growth_latest: float | None = None
    gdp_growth_latest: float | None = None
    as_of_date: str | None = None
    fetch_errors: list[str] = Field(default_factory=list)


class AnalystInputPayload(StrictModel):
    tradingsymbol: str
    exchange: str
    quantity: int
    average_price: float
    last_price: float
    pnl: float
    pnl_pct: float
    current_weight_pct: float
    target_weight_pct: float
    drift: float
    high_52w: float | str = Field(alias="52w_high")
    low_52w: float | str = Field(alias="52w_low")
    current_vs_52w_high_pct: float | str
    macro_context: str = ""
    yfinance_data: dict[str, object] = Field(default_factory=dict)


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
        normalized = str(value).strip().lower()
        mapping = {
            "medium-high": "high",
            "medium-low": "low",
        }
        normalized = mapping.get(normalized, normalized)
        return {"low": "Low", "medium": "Medium", "high": "High"}.get(normalized, normalized)


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
    source_map: dict[str, str] = Field(default_factory=dict)


class CardMeta(StrictModel):
    ticker: str
    exchange: str
    isin: str | None = None
    company_name: str
    listing_date: str | None = None
    face_value: float | None = None
    sector: str | None = None
    industry: str | None = None
    industry_macro: str | None = None
    industry_4l: str | None = None
    index_memberships: list[str] = Field(default_factory=list)
    is_fno: bool = False
    is_slb: bool = False
    is_under_surveillance: bool = False
    surveillance_stage: str | None = None
    fetched_at: datetime


class CardPriceData(StrictModel):
    cmp: float | None = None
    prev_close: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    vwap: float | None = None
    change_pct_today: float | None = None
    week_52_high: float | None = None
    week_52_low: float | None = None
    week_52_high_date: str | None = None
    week_52_low_date: str | None = None
    vs_52w_high_pct: float | None = None
    dma_50: float | None = None
    dma_200: float | None = None
    vs_50dma_pct: float | None = None
    vs_200dma_pct: float | None = None
    alpha_vs_nifty_52w_pct: float | None = None
    beta: float | None = None
    annual_volatility_pct: float | None = None


class CardValuation(StrictModel):
    trailing_pe: float | None = None
    forward_pe: float | None = None
    sector_pe: float | None = None
    pe_premium_to_sector_pct: float | None = None
    price_to_book: float | None = None
    price_to_sales: float | None = None
    ev_ebitda: float | None = None
    ev_revenue: float | None = None
    peg_ratio: float | None = None
    earnings_yield_pct: float | None = None
    analyst_target_mean: float | None = None
    analyst_target_median: float | None = None
    analyst_upside_pct: float | None = None
    analyst_count: int | None = None
    analyst_consensus: str | None = None


class CardFinancials(StrictModel):
    market_cap: float | None = None
    free_float_market_cap: float | None = None
    enterprise_value: float | None = None
    total_revenue: float | None = None
    revenue_per_share: float | None = None
    gross_profit: float | None = None
    gross_margin_pct: float | None = None
    ebitda: float | None = None
    ebitda_margin_pct: float | None = None
    operating_margin_pct: float | None = None
    net_income: float | None = None
    profit_margin_pct: float | None = None
    trailing_eps: float | None = None
    forward_eps: float | None = None
    total_cash: float | None = None
    total_debt: float | None = None
    net_cash: float | None = None
    debt_to_equity: float | None = None
    revenue_growth_pct: float | None = None
    earnings_growth_pct: float | None = None
    book_value_per_share: float | None = None


class CardNSEQuarter(StrictModel):
    period: str
    income: float | None = None
    pat: float | None = None
    eps: float | None = None
    pbt: float | None = None
    audited: str | None = None


class CardNSEQuarterly(StrictModel):
    quarters: list[CardNSEQuarter] = Field(default_factory=list)
    revenue_qoq_pct: float | None = None
    revenue_yoy_pct: float | None = None
    eps_qoq_pct: float | None = None
    pat_qoq_pct: float | None = None


class CardQuality(StrictModel):
    roe_proxy_pct: float | None = None
    roce_proxy_pct: float | None = None
    delivery_pct: float | None = None
    impact_cost: float | None = None
    var_applicable_margin_pct: float | None = None
    governance_score: float | None = None
    audit_risk: int | None = None
    board_risk: int | None = None
    compensation_risk: int | None = None
    overall_risk_score: int | None = None


class CardOwnership(StrictModel):
    shares_outstanding: float | None = None
    float_shares: float | None = None
    float_ratio_pct: float | None = None
    institutional_holding_pct: float | None = None
    insider_holding_pct: float | None = None
    promoter_holding_pct: float | None = None
    promoter_holding_qoq_change: float | None = None
    shareholding_history: list[dict] = Field(default_factory=list)


class CardDividendCorporate(StrictModel):
    dividend_yield_pct: float | None = None
    payout_ratio: float | None = None
    five_yr_avg_dividend_yield: float | None = None
    last_dividend_amount: float | None = None
    last_dividend_date: str | None = None
    next_earnings_date: str | None = None
    recent_corporate_actions: list[dict] = Field(default_factory=list)


class CardTechnicals(StrictModel):
    vwap_signal_pct: float | None = None
    pre_open_sentiment_pct: float | None = None
    price_band_type: str | None = None
    dist_from_lower_circuit_pct: float | None = None
    dist_from_upper_circuit_pct: float | None = None
    delivery_signal: str | None = None  # "High" | "Medium" | "Low"


class CompanyDataCard(StrictModel):
    generated_at: datetime
    source_model: str
    exchange: str
    ticker: str
    macro_context: str = ""
    meta: CardMeta
    price_data: CardPriceData
    valuation: CardValuation
    financials: CardFinancials
    nse_quarterly: CardNSEQuarterly
    quality: CardQuality
    ownership: CardOwnership
    dividends_corporate: CardDividendCorporate
    technical_signals: CardTechnicals
    analysis: AnalystReportCard


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
