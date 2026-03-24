from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
