from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from models.portfolio import StrictModel


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
