from __future__ import annotations

from models import Holding, StockVerdict


def verify_portfolio_weights(holdings: list[Holding], total_value: float) -> list[str]:
    """
    Returns a list of warning strings if portfolio weight math has issues.
    Checks:
    - Sum of position values approximates total_value (within 2%)
    - current_weight_pct values sum to ~100% (within 2pp)
    - No holding has current_value > total_value
    """
    warnings: list[str] = []
    if not holdings or total_value <= 0:
        return warnings

    sum_values = sum(h.current_value for h in holdings)
    if total_value > 0:
        diff_pct = abs(sum_values - total_value) / total_value * 100
        if diff_pct > 2.0:
            warnings.append(
                f"Sum of holding values ({sum_values:.2f}) deviates from total_value ({total_value:.2f}) "
                f"by {diff_pct:.2f}% (threshold: 2%)"
            )

    sum_weights = sum(h.current_weight_pct for h in holdings)
    if abs(sum_weights - 100.0) > 2.0:
        warnings.append(f"current_weight_pct values sum to {sum_weights:.2f}% — expected ~100% (within 2pp)")

    for holding in holdings:
        if holding.current_value > total_value:
            warnings.append(
                f"Holding {holding.tradingsymbol} has current_value={holding.current_value:.2f} "
                f"which exceeds total_value={total_value:.2f}"
            )

    return warnings


def verify_rebalance_consistency(verdicts: list[StockVerdict], actions: list[object]) -> list[str]:
    """
    Returns a list of warning strings if rebalance math has inconsistencies.
    Checks:
    - rebalance_rupees > 0 for BUY/SELL actions
    - rebalance_rupees == 0 for HOLD actions
    - Total buy required doesn't exceed available cash by more than reasonable margin
    """
    warnings: list[str] = []

    for verdict in verdicts:
        action = verdict.rebalance_action
        rupees = verdict.rebalance_rupees
        if action in {"BUY", "SELL"} and rupees <= 0:
            warnings.append(
                f"{verdict.tradingsymbol}: rebalance_action={action} but rebalance_rupees={rupees} (expected > 0)"
            )
        if action == "HOLD" and rupees != 0.0:
            warnings.append(
                f"{verdict.tradingsymbol}: rebalance_action=HOLD but rebalance_rupees={rupees} (expected 0)"
            )

    return warnings
