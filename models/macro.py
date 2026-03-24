from __future__ import annotations

from pydantic import Field

from models.portfolio import StrictModel


class MacroContext(StrictModel):
    cpi_headline_yoy: float | None = None  # All India CPI General YoY inflation %
    cpi_as_of: str | None = None  # e.g. "December 2025"
    iip_growth_latest: float | None = None  # IIP General growth % (latest annual)
    iip_capital_goods_growth: float | None = None  # IIP Capital Goods growth % (latest annual)
    iip_as_of: str | None = None  # e.g. "2024-25"
    gdp_growth_latest: float | None = None  # GDP growth at constant prices % (latest quarter)
    gdp_as_of: str | None = None  # e.g. "Q3 2024-25"
    unemployment_rate: float | None = None  # PLFS unemployment rate % (latest annual)
    unemployment_as_of: str | None = None  # e.g. "2023-24"
    as_of_date: str | None = None  # overall latest date label
    fetch_errors: list[str] = Field(default_factory=list)
