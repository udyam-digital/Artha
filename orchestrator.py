from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from collections.abc import Callable
from pathlib import Path

from anthropic import AsyncAnthropic

from company_analysis import get_company_artifact_and_verdict, is_company_artifact_fresh
from config import Settings
from kite_runtime import KiteSyncResult, build_kite_client, sync_kite_data, sync_kite_data_with_client
from models import Holding, PortfolioReport, PortfolioSnapshot, RebalancingAction, StockVerdict, Verdict
from reliability import FullRunFailed, RetryFailure, run_with_retries
from rebalance import PASSIVE_INSTRUMENTS, calculate_rebalancing_actions
from snapshot_store import company_analysis_path, load_company_analysis_artifact
from tools import ToolExecutionError, kite_get_price_history
from usage_tracking import log_estimated_input_tokens, record_anthropic_usage, record_run_error


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
        verdict.rebalance_reasoning = _hold_reasoning(verdict)
        return verdict

    verdict.rebalance_action = action.action
    verdict.rebalance_rupees = round(action.rupee_amount, 2)
    verdict.rebalance_reasoning = _action_reasoning(action.action)
    return verdict


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
    kite_client: object | None = None,
) -> dict[str, dict[str, float | str]]:
    async def fetch_for_holding(active_kite_client: object, holding: Holding) -> dict[str, float | str]:
        try:
            return await run_with_retries(
                lambda: kite_get_price_history(
                    active_kite_client,
                    tradingsymbol=holding.tradingsymbol,
                    instrument_token=holding.instrument_token,
                ),
                attempts=settings.transient_retry_attempts,
                base_delay_seconds=settings.transient_retry_base_delay_seconds,
                phase="price_history",
                ticker=holding.tradingsymbol,
            )
        except RetryFailure as exc:
            if isinstance(exc.cause, ToolExecutionError) and "No historical data available" in str(exc.cause):
                logger.warning(
                    "[%s] price history unavailable; continuing with reduced context",
                    holding.tradingsymbol,
                )
                return _default_price_context()
            raise

    if kite_client is None:
        async with build_kite_client(settings) as owned_kite_client:
            results = await asyncio.gather(*[fetch_for_holding(owned_kite_client, holding) for holding in holdings])
    else:
        results = await asyncio.gather(*[fetch_for_holding(kite_client, holding) for holding in holdings])
    return {holding.tradingsymbol: result for holding, result in zip(holdings, results, strict=True)}


def _default_price_context() -> dict[str, float]:
    return {
        "52w_high": 0.0,
        "52w_low": 0.0,
        "current_vs_52w_high_pct": 0.0,
        "price_1y_ago": 0.0,
        "price_change_1y_pct": 0.0,
    }


def _holding_requires_refresh(*, holding: Holding, settings: Settings) -> bool:
    try:
        cached = load_company_analysis_artifact(holding.tradingsymbol, settings=settings)
    except FileNotFoundError:
        return True
    except Exception:
        return True
    return not (
        cached.ticker.upper() == holding.tradingsymbol.upper()
        and is_company_artifact_fresh(artifact=cached, settings=settings)
    )


async def _build_portfolio_summary(
    *,
    client: AsyncAnthropic,
    settings: Settings,
    verdicts: list[StockVerdict],
    snapshot: PortfolioSnapshot,
    mf_symbols: list[str],
    errors: list[str],
) -> str:
    payload = {
        "portfolio_value": snapshot.total_value,
        "available_cash": snapshot.available_cash,
        "equity_holdings": [holding.tradingsymbol for holding in snapshot.holdings],
        "mf_holdings": mf_symbols,
        "verdicts": [verdict.model_dump(mode="json") for verdict in verdicts],
        "errors": errors,
    }
    subject = "portfolio" if len(verdicts) != 1 else "single-stock portfolio"
    messages = [
        {
            "role": "user",
            "content": (
                f"Write a concise 3-5 sentence summary for Saksham's Indian equity {subject}. "
                "Do not redo analysis. Use only the supplied verdict JSON and mention the main concentration, "
                "risk, and rebalance takeaways.\n"
                f"Input JSON:\n{json.dumps(payload, ensure_ascii=True)}"
            ),
        }
    ]
    log_estimated_input_tokens(label="[portfolio_summary]", messages=messages)
    response = await client.messages.create(
        model=settings.model,
        max_tokens=min(settings.max_tokens, settings.summary_max_tokens),
        messages=messages,
    )
    record_anthropic_usage(
        settings=settings,
        label="portfolio_summary",
        model=settings.model,
        response=response,
        metadata={
            "phase": "portfolio_summary",
            "equity_count": len(snapshot.holdings),
            "mf_count": len(mf_symbols),
            "error_count": len(errors),
        },
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
    sync_result: KiteSyncResult | None = None,
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
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    skills_content = _load_analyst_prompt()
    price_context_by_symbol: dict[str, dict[str, float | str]] = {}

    try:
        if sync_result is None:
            async with build_kite_client(settings) as kite_client:
                sync_result = await run_with_retries(
                    lambda: sync_kite_data_with_client(kite_client, settings=settings),
                    attempts=settings.transient_retry_attempts,
                    base_delay_seconds=settings.transient_retry_base_delay_seconds,
                    phase="kite_sync",
                )
                equity_holdings = [
                    holding
                    for holding in sync_result.portfolio_snapshot.holdings
                    if holding.tradingsymbol not in PASSIVE_INSTRUMENTS
                ]
                holdings_needing_context = [
                    holding for holding in equity_holdings if _holding_requires_refresh(holding=holding, settings=settings)
                ]
                if holdings_needing_context:
                    price_context_by_symbol = await _price_contexts(
                        settings=settings,
                        holdings=holdings_needing_context,
                        kite_client=kite_client,
                    )
        else:
            equity_holdings = [
                holding
                for holding in sync_result.portfolio_snapshot.holdings
                if holding.tradingsymbol not in PASSIVE_INSTRUMENTS
            ]
            logger.info("Using saved same-day Kite snapshots; skipping fresh sync and price-history fetch.")
    except RetryFailure as exc:
        error_path = record_run_error(
            settings=settings,
            phase=exc.phase,
            error=exc.cause,
            retries_used=exc.retries_used,
            ticker=exc.ticker,
            partial_artifact_path=exc.partial_artifact_path,
        )
        raise FullRunFailed(
            phase=exc.phase,
            message=str(exc.cause),
            retries_used=exc.retries_used,
            ticker=exc.ticker,
            error_log_path=error_path,
            partial_artifact_path=exc.partial_artifact_path,
        ) from exc

    semaphore = asyncio.Semaphore(settings.analyst_parallelism)

    async def bounded_analyse(holding: Holding, index: int) -> StockVerdict:
        stagger_seconds = max(settings.analyst_min_start_interval_seconds, 0.0)
        if stagger_seconds > 0:
            await asyncio.sleep(index * stagger_seconds)
        async with semaphore:
            _, verdict, from_cache = await run_with_retries(
                lambda: get_company_artifact_and_verdict(
                    holding=holding,
                    price_context=price_context_by_symbol.get(holding.tradingsymbol, _default_price_context()),
                    skills_content=skills_content,
                    client=client,
                    settings=settings,
                ),
                attempts=settings.transient_retry_attempts,
                base_delay_seconds=settings.transient_retry_base_delay_seconds,
                phase="analyst",
                ticker=holding.tradingsymbol,
                partial_artifact_path=company_analysis_path(holding.tradingsymbol, settings=settings),
            )
            if from_cache:
                verdict.analysis_duration_seconds = 0.0
            return verdict

    task_to_symbol = {
        asyncio.create_task(bounded_analyse(holding, index)): holding.tradingsymbol
        for index, holding in enumerate(equity_holdings)
    }

    ordered_verdicts: dict[str, StockVerdict] = {}
    completed = 0
    total = len(task_to_symbol)
    for task in asyncio.as_completed(task_to_symbol):
        try:
            verdict = await task
        except RetryFailure as exc:
            error_path = record_run_error(
                settings=settings,
                phase=exc.phase,
                error=exc.cause,
                retries_used=exc.retries_used,
                ticker=exc.ticker,
                partial_artifact_path=exc.partial_artifact_path,
            )
            raise FullRunFailed(
                phase=exc.phase,
                message=str(exc.cause),
                retries_used=exc.retries_used,
                ticker=exc.ticker,
                error_log_path=error_path,
                partial_artifact_path=exc.partial_artifact_path,
            ) from exc
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
    if errors:
        first_error = next((verdict for verdict in verdicts if verdict.error), None)
        error_path = record_run_error(
            settings=settings,
            phase="analyst",
            error=errors[0],
            retries_used=0,
            ticker=first_error.tradingsymbol if first_error else None,
            partial_artifact_path=company_analysis_path(first_error.tradingsymbol, settings=settings) if first_error else None,
        )
        raise FullRunFailed(
            phase="analyst",
            message=errors[0],
            retries_used=0,
            ticker=first_error.tradingsymbol if first_error else None,
            error_log_path=error_path,
            partial_artifact_path=company_analysis_path(first_error.tradingsymbol, settings=settings) if first_error else None,
        )

    for verdict in verdicts:
        _merge_action_into_verdict(verdict, action_by_symbol.get(verdict.tradingsymbol))
        final_actions[verdict.tradingsymbol] = _verdict_to_action(verdict, holding_by_symbol[verdict.tradingsymbol])

    try:
        portfolio_summary = await run_with_retries(
            lambda: _build_portfolio_summary(
                client=client,
                settings=settings,
                verdicts=verdicts,
                snapshot=sync_result.portfolio_snapshot,
                mf_symbols=[holding.tradingsymbol for holding in sync_result.mf_snapshot.holdings],
                errors=[error for error in errors if error],
            ),
            attempts=settings.transient_retry_attempts,
            base_delay_seconds=settings.transient_retry_base_delay_seconds,
            phase="portfolio_summary",
        )
    except RetryFailure as exc:
        error_path = record_run_error(
            settings=settings,
            phase=exc.phase,
            error=exc.cause,
            retries_used=exc.retries_used,
            ticker=exc.ticker,
            partial_artifact_path=exc.partial_artifact_path,
        )
        raise FullRunFailed(
            phase=exc.phase,
            message=str(exc.cause),
            retries_used=exc.retries_used,
            ticker=exc.ticker,
            error_log_path=error_path,
            partial_artifact_path=exc.partial_artifact_path,
        ) from exc

    total_buy_required = sum(
        verdict.rebalance_rupees for verdict in verdicts if verdict.rebalance_action == "BUY"
    )
    total_sell_required = sum(
        verdict.rebalance_rupees for verdict in verdicts if verdict.rebalance_action == "SELL"
    )
    elapsed = time.perf_counter() - started
    logger.info(
        "Full analysis completed in %.1fs across %s analyst sub-agents with parallelism=%s",
        elapsed,
        len(verdicts),
        settings.analyst_parallelism,
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


async def run_single_company_analysis(
    *,
    settings: Settings,
    ticker: str,
    exchange: str = "NSE",
) -> PortfolioReport:
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    skills_content = _load_analyst_prompt()
    snapshot: PortfolioSnapshot | None = None
    try:
        from snapshot_store import load_latest_portfolio_snapshot

        snapshot = load_latest_portfolio_snapshot(settings)
    except Exception:
        snapshot = None

    holding = None
    if snapshot is not None:
        holding = next(
            (
                item
                for item in snapshot.holdings
                if item.tradingsymbol == ticker.upper() and item.tradingsymbol not in PASSIVE_INSTRUMENTS
            ),
            None,
        )

    if holding is None:
        holding = Holding(
            tradingsymbol=ticker.upper(),
            exchange=exchange.upper(),
            quantity=0,
            average_price=0.0,
            last_price=0.0,
            current_value=0.0,
            current_weight_pct=0.0,
            target_weight_pct=0.0,
            pnl=0.0,
            pnl_pct=0.0,
            instrument_token=0,
        )

    price_context: dict[str, float | str] = {"52w_high": "N/A", "52w_low": "N/A", "current_vs_52w_high_pct": "N/A"}
    if holding.instrument_token > 0:
        price_context = (await _price_contexts(settings=settings, holdings=[holding])).get(holding.tradingsymbol, price_context)

    _, verdict, from_cache = await get_company_artifact_and_verdict(
        holding=holding,
        price_context=price_context,
        skills_content=skills_content,
        client=client,
        settings=settings,
    )
    if from_cache:
        verdict.analysis_duration_seconds = 0.0

    portfolio_total_value = snapshot.total_value if snapshot is not None else holding.current_value
    portfolio_cash = snapshot.available_cash if snapshot is not None else 0.0
    actions = calculate_rebalancing_actions(
        holdings=[holding] if holding.tradingsymbol not in PASSIVE_INSTRUMENTS else [],
        total_value=portfolio_total_value,
        available_cash=portfolio_cash,
    )
    action = next((item for item in actions if item.tradingsymbol == holding.tradingsymbol), None)
    _merge_action_into_verdict(verdict, action)

    report_snapshot = PortfolioSnapshot(
        fetched_at=snapshot.fetched_at if snapshot is not None else datetime.now(timezone.utc),
        total_value=portfolio_total_value,
        available_cash=portfolio_cash,
        holdings=[holding],
    )
    summary = await _build_portfolio_summary(
        client=client,
        settings=settings,
        verdicts=[verdict],
        snapshot=report_snapshot,
        mf_symbols=[],
        errors=[],
    )
    return PortfolioReport(
        generated_at=datetime.now(timezone.utc),
        portfolio_snapshot=report_snapshot,
        verdicts=[verdict],
        portfolio_summary=summary,
        total_buy_required=verdict.rebalance_rupees if verdict.rebalance_action == "BUY" else 0.0,
        total_sell_required=verdict.rebalance_rupees if verdict.rebalance_action == "SELL" else 0.0,
        errors=[verdict.error] if verdict.error else [],
    )
