from __future__ import annotations

from datetime import datetime

from pydantic import Field

from models.analyst import AnalystReportCard
from models.portfolio import StrictModel


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


class CardRecentFilings(StrictModel):
    """Recent corporate announcements from NSE/BSE (last 30 days). Requires nse-bse-mcp."""

    filings: list[dict] = Field(default_factory=list)
    has_guidance_update: bool = False
    has_management_change: bool = False
    has_pledging_update: bool = False
    has_audit_issue: bool = False
    fetch_errors: list[str] = Field(default_factory=list)


class CardBulkDeals(StrictModel):
    """Recent bulk deals from NSE (last 30 days). Requires nse-bse-mcp."""

    deals: list[dict] = Field(default_factory=list)
    net_direction: str = "None"  # "Buying", "Selling", "Mixed", "None"
    total_buy_qty: float = 0.0
    total_sell_qty: float = 0.0
    fetch_errors: list[str] = Field(default_factory=list)


class CompanyDataCard(StrictModel):
    generated_at: datetime
    source_model: str
    exchange: str
    ticker: str
    meta: CardMeta
    price_data: CardPriceData
    valuation: CardValuation
    financials: CardFinancials
    nse_quarterly: CardNSEQuarterly
    quality: CardQuality
    ownership: CardOwnership
    dividends_corporate: CardDividendCorporate
    technical_signals: CardTechnicals
    recent_filings: CardRecentFilings = Field(default_factory=CardRecentFilings)
    bulk_deals: CardBulkDeals = Field(default_factory=CardBulkDeals)
    analysis: AnalystReportCard
