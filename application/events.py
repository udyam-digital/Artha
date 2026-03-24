from __future__ import annotations

from collections.abc import Callable
from typing import Literal, TypedDict


class PhaseEvent(TypedDict):
    type: Literal["phase"]
    phase: str  # "kite_sync" | "analyst" | "rebalance" | "summary"
    label: str
    total: int  # 0 except for "analyst" which carries the holding count


class AnalystCompleteEvent(TypedDict):
    type: Literal["analyst_complete"]
    completed: int
    total: int
    ticker: str
    verdict: str
    confidence: str
    thesis_intact: bool
    pnl_pct: float
    duration_seconds: float
    bull_case: str
    red_flags: list[str]


RunEvent = PhaseEvent | AnalystCompleteEvent
RunEventCallback = Callable[[RunEvent], None]
