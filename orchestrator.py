from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path

from anthropic import AsyncAnthropic

from analyst import analyse_stock
from config import Settings
from kite_runtime import KiteSyncResult, build_kite_client, sync_kite_data
from models import Holding, PortfolioReport, RebalancingAction, StockVerdict, Verdict
from rebalance import PASSIVE_INSTRUMENTS, calculate_rebalancing_actions
from tools import kite_get_price_history


logger = logging.getLogger(__name__)


def _load_analyst_prompt() -> str:
    return (Path("skills") / "analyst_prompt.md").read_text(encoding="utf-8")


def _should_gate_to_hold(verdict: Verdict, thesis_intact: bool) -> bool:
    if verdict == Verdict.HOLD:
        return True
    if verdict in {Verdict.BUY, Verdict.STRONG_BUY}:
        return not thesis_intact
    if verdict in {Verdict.SELL, Verdict.STRONG_SELL}:
        return thesis_intact
    return True


def _merge_action_into_verdict(verdict: StockVerdict, action: RebalancingAction | None) -> StockVerdict:
    if action is None:
        verdict.rebalance_action = "HOLD"
        verdict.rebalance_rupees = 0.0
        verdict.rebalance_reasoning = "No deterministic rebalance action was generated for this holding."
        return verdict

    if action.action == "HOLD" or _should_gate_to_hold(verdict.verdict, verdict.thesis_intact):
        verdict.rebalance_action = "HOLD"
        verdict.rebalance_rupees = 0.0
        verdict.rebalance_reasoning = (
            f"Drift math suggested {action.action}, but the final action is HOLD because the verdict is "
            f"{verdict.verdict} with thesis_intact={verdict.thesis_intact}."
        )
        return verdict

    verdict.rebalance_action = action.action
    verdict.rebalance_rupees = round(action.rupee_amount, 2)
    verdict.rebalance_reasoning = (
        f"Verdict {verdict.verdict} supports a {action.action} and drift is {action.drift_pct:+.1f}% "
        f"versus target, so the deterministic sizing is used."
    )
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


async def _price_contexts(
    *,
    settings: Settings,
    holdings: list[Holding],
) -> dict[str, dict[str, float | str]]:
    async with build_kite_client(settings) as kite_client:
        results = await asyncio.gather(
            *[
                kite_get_price_history(
                    kite_client,
                    tradingsymbol=holding.tradingsymbol,
                    instrument_token=holding.instrument_token,
                )
                for holding in holdings
            ]
        )
    return {holding.tradingsymbol: result for holding, result in zip(holdings, results, strict=True)}


async def _build_portfolio_summary(
    *,
    client: AsyncAnthropic,
    settings: Settings,
    verdicts: list[StockVerdict],
    sync_result: KiteSyncResult,
    errors: list[str],
) -> str:
    payload = {
        "portfolio_value": sync_result.portfolio_snapshot.total_value,
        "available_cash": sync_result.portfolio_snapshot.available_cash,
        "equity_holdings": [holding.tradingsymbol for holding in sync_result.portfolio_snapshot.holdings],
        "mf_holdings": [holding.tradingsymbol for holding in sync_result.mf_snapshot.holdings],
        "verdicts": [verdict.model_dump(mode="json") for verdict in verdicts],
        "errors": errors,
    }
    response = await client.messages.create(
        model=settings.model,
        max_tokens=min(settings.max_tokens, 700),
        messages=[
            {
                "role": "user",
                "content": (
                    "Write a concise 3-5 sentence portfolio summary for Saksham's Indian equity portfolio. "
                    "Do not redo analysis. Use only the supplied verdict JSON and mention the main concentration, "
                    "risk, and rebalance takeaways.\n"
                    f"Input JSON:\n{json.dumps(payload, ensure_ascii=True)}"
                ),
            }
        ],
    )
    text_parts = [
        getattr(block, "text", "")
        for block in getattr(response, "content", [])
        if getattr(block, "type", None) == "text"
    ]
    return "\n".join(text_parts).strip() or "No portfolio summary generated."


async def run_full_analysis(
    settings: Settings,
    progress_callback: Callable[[int, int, StockVerdict], None] | None = None,
) -> PortfolioReport:
    """
    Orchestrates the full Artha pipeline:
    1. Fetch live portfolio from Kite
    2. Filter: exclude ETFs from analysis
    3. Run analyst sub-agents in parallel for all equity holdings
    4. Collect verdicts
    5. Run rebalance math
    6. Synthesize final report with one orchestrator Claude call
    7. Return PortfolioReport
    """
    started = time.perf_counter()
    sync_result = await sync_kite_data(settings=settings)
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    skills_content = _load_analyst_prompt()
    equity_holdings = [
        holding
        for holding in sync_result.portfolio_snapshot.holdings
        if holding.tradingsymbol not in PASSIVE_INSTRUMENTS
    ]
    price_context_by_symbol = await _price_contexts(settings=settings, holdings=equity_holdings)

    semaphore = asyncio.Semaphore(5)

    async def bounded_analyse(holding: Holding) -> StockVerdict:
        async with semaphore:
            return await analyse_stock(
                holding=holding,
                portfolio_total_value=sync_result.portfolio_snapshot.total_value,
                price_context=price_context_by_symbol.get(holding.tradingsymbol, {}),
                skills_content=skills_content,
                client=client,
                config=settings,
            )

    task_to_symbol = {
        asyncio.create_task(bounded_analyse(holding)): holding.tradingsymbol
        for holding in equity_holdings
    }

    ordered_verdicts: dict[str, StockVerdict] = {}
    completed = 0
    total = len(task_to_symbol)
    for task in asyncio.as_completed(task_to_symbol):
        verdict = await task
        completed += 1
        ordered_verdicts[verdict.tradingsymbol] = verdict
        if progress_callback is not None:
            progress_callback(completed, total, verdict)

    verdicts = [ordered_verdicts[holding.tradingsymbol] for holding in equity_holdings if holding.tradingsymbol in ordered_verdicts]

    math_actions = calculate_rebalancing_actions(
        holdings=equity_holdings,
        total_value=sync_result.portfolio_snapshot.total_value,
        available_cash=sync_result.portfolio_snapshot.available_cash,
    )
    action_by_symbol = {action.tradingsymbol: action for action in math_actions}
    holding_by_symbol = {holding.tradingsymbol: holding for holding in equity_holdings}
    final_actions: dict[str, RebalancingAction] = {}
    errors = [verdict.error for verdict in verdicts if verdict.error]

    for verdict in verdicts:
        _merge_action_into_verdict(verdict, action_by_symbol.get(verdict.tradingsymbol))
        final_actions[verdict.tradingsymbol] = _verdict_to_action(verdict, holding_by_symbol[verdict.tradingsymbol])

    portfolio_summary = await _build_portfolio_summary(
        client=client,
        settings=settings,
        verdicts=verdicts,
        sync_result=sync_result,
        errors=[error for error in errors if error],
    )

    total_buy_required = sum(
        verdict.rebalance_rupees for verdict in verdicts if verdict.rebalance_action == "BUY"
    )
    total_sell_required = sum(
        verdict.rebalance_rupees for verdict in verdicts if verdict.rebalance_action == "SELL"
    )
    elapsed = time.perf_counter() - started
    logger.info(
        "Full analysis completed in %.1fs across %s analyst sub-agents",
        elapsed,
        len(verdicts),
    )
    return PortfolioReport(
        generated_at=sync_result.portfolio_snapshot.fetched_at,
        portfolio_snapshot=sync_result.portfolio_snapshot,
        verdicts=verdicts,
        portfolio_summary=portfolio_summary,
        total_buy_required=total_buy_required,
        total_sell_required=total_sell_required,
        errors=[error for error in errors if error],
    )
