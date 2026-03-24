from __future__ import annotations

from models import StockVerdict


def format_rupees(amount: float) -> str:
    return f"\u20b9{amount:,.0f}"


def _verdict_to_action_text(verdict: StockVerdict) -> str:
    if verdict.rebalance_action == "HOLD":
        return "HOLD —"
    return f"{verdict.rebalance_action} {format_rupees(verdict.rebalance_rupees)}"


def _thesis_text(verdict: StockVerdict) -> str:
    return "✓ Intact" if verdict.thesis_intact else "✗ Weak"
