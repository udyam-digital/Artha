from __future__ import annotations

from models import Holding, RebalancingAction

PASSIVE_INSTRUMENTS = {"LIQUIDBEES", "NIFTYBEES", "GOLDCASE", "SILVERCASE"}


def is_passive_instrument(tradingsymbol: str) -> bool:
    return tradingsymbol.upper() in PASSIVE_INSTRUMENTS


def calculate_drift(holdings: list[Holding]) -> dict[str, float]:
    return {
        holding.tradingsymbol: holding.current_weight_pct - holding.target_weight_pct
        for holding in holdings
        if not is_passive_instrument(holding.tradingsymbol)
    }


def _urgency_for_drift(drift_pct: float) -> str:
    magnitude = abs(drift_pct)
    if magnitude > 5:
        return "HIGH"
    if magnitude > 3:
        return "MEDIUM"
    return "LOW"


def calculate_rebalancing_actions(
    holdings: list[Holding],
    total_value: float,
    available_cash: float,
    drift_threshold: float = 2.0,
) -> list[RebalancingAction]:
    del available_cash
    actions: list[RebalancingAction] = []

    for holding in holdings:
        if is_passive_instrument(holding.tradingsymbol):
            continue

        drift_pct = holding.current_weight_pct - holding.target_weight_pct
        action = "HOLD"
        rupee_amount = 0.0
        quantity_approx = 0
        reasoning = "Position is within the allowed drift band."

        if drift_pct < -drift_threshold:
            action = "BUY"
            rupee_amount = abs(drift_pct / 100.0) * total_value
            quantity_approx = int(rupee_amount / holding.last_price) if holding.last_price > 0 else 0
            reasoning = "Position is underweight versus target."
        elif drift_pct > drift_threshold:
            action = "SELL"
            rupee_amount = abs(drift_pct / 100.0) * total_value
            quantity_approx = int(rupee_amount / holding.last_price) if holding.last_price > 0 else 0
            reasoning = "Position is overweight versus target."

        actions.append(
            RebalancingAction(
                tradingsymbol=holding.tradingsymbol,
                action=action,
                current_weight_pct=holding.current_weight_pct,
                target_weight_pct=holding.target_weight_pct,
                drift_pct=drift_pct,
                rupee_amount=rupee_amount,
                quantity_approx=quantity_approx,
                reasoning=reasoning,
                urgency=_urgency_for_drift(drift_pct),
            )
        )

    return actions
