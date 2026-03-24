from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from models.portfolio import AnalystStockSnapshot, StrictModel


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
