from __future__ import annotations

from models import Holding, RebalancingAction, StockVerdict, Verdict


def _should_gate_to_hold(verdict: Verdict, thesis_intact: bool) -> bool:
    if verdict == Verdict.HOLD:
        return True
    if verdict in {Verdict.BUY, Verdict.STRONG_BUY}:
        return not thesis_intact
    if verdict == Verdict.STRONG_SELL:
        return thesis_intact
    if verdict == Verdict.SELL:
        return False
    return True


def _hold_reasoning(verdict: StockVerdict) -> str:
    if verdict.verdict == Verdict.HOLD and verdict.thesis_intact:
        return "Current conviction is unchanged. No rebalance action now; monitor drift versus target."
    return "Current conviction does not support rebalancing this position now; monitor drift versus target."


def _action_reasoning(action: str) -> str:
    if action == "BUY":
        return "Underweight versus target. Current conviction supports adding more."
    if action == "SELL":
        return "Overweight versus target. Current conviction supports trimming."
    return "Current conviction is unchanged. No rebalance action now; monitor drift versus target."


def _merge_action_into_verdict(verdict: StockVerdict, action: RebalancingAction | None) -> StockVerdict:
    if action is None:
        verdict.rebalance_action = "HOLD"
        verdict.rebalance_rupees = 0.0
        verdict.rebalance_reasoning = "No deterministic rebalance action was generated for this holding."
        return verdict

    if action.action == "HOLD" or _should_gate_to_hold(verdict.verdict, verdict.thesis_intact):
        verdict.rebalance_action = "HOLD"
        verdict.rebalance_rupees = 0.0
        verdict.rebalance_reasoning = _hold_reasoning(verdict)
        return verdict

    verdict.rebalance_action = action.action
    verdict.rebalance_rupees = round(action.rupee_amount, 2)
    verdict.rebalance_reasoning = _action_reasoning(action.action)
    return verdict


def _verdict_to_action(verdict: StockVerdict, holding: Holding) -> RebalancingAction:
    drift_pct = holding.current_weight_pct - holding.target_weight_pct
    quantity_approx = int(verdict.rebalance_rupees / holding.last_price) if holding.last_price > 0 else 0
    urgency = "HIGH" if abs(drift_pct) > 5 else "MEDIUM" if abs(drift_pct) > 3 else "LOW"
    return RebalancingAction(
        tradingsymbol=holding.tradingsymbol,
        action=verdict.rebalance_action,
        current_weight_pct=holding.current_weight_pct,
        target_weight_pct=holding.target_weight_pct,
        drift_pct=drift_pct,
        rupee_amount=verdict.rebalance_rupees,
        quantity_approx=quantity_approx,
        reasoning=verdict.rebalance_reasoning,
        urgency=urgency,
    )
