from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

from anthropic import AsyncAnthropic

from analysis.company import get_company_artifact_and_verdict
from analysis.verify import verify_portfolio_weights
from application.context_builders import (  # noqa: F401
    _build_analyst_prompt,
    _build_macro_summary,
    _build_portfolio_summary,
    _default_price_context,
    _price_contexts,
)
from application.events import (  # noqa: F401
    AnalystCompleteEvent,
    PhaseEvent,
    RunEvent,
    RunEventCallback,
)
from application.run_helpers import (  # noqa: F401
    _holding_requires_refresh,
    _run_analyst_fan_out,
    _save_run_manifest_safe,
)
from application.verdict_ops import (  # noqa: F401
    _action_reasoning,
    _hold_reasoning,
    _merge_action_into_verdict,
    _should_gate_to_hold,
    _verdict_to_action,
)
from config import Settings
from kite.runtime import KiteSyncResult, build_kite_client, sync_kite_data_with_client
from models import Holding, PortfolioReport, PortfolioSnapshot, RebalancingAction
from observability.langfuse_client import init_langfuse
from observability.token_budget import TokenBudgetManager
from observability.usage import record_run_error
from persistence.store import company_analysis_path
from providers.nse_bse import get_earnings_calendar
from rebalance import PASSIVE_INSTRUMENTS, calculate_rebalancing_actions
from reliability import FullRunFailed, RetryFailure, run_with_retries

logger = logging.getLogger(__name__)


def build_rebalance_only_report(snapshot: PortfolioSnapshot) -> tuple[PortfolioReport, list[RebalancingAction]]:
    actions = calculate_rebalancing_actions(
        holdings=snapshot.holdings,
        total_value=snapshot.total_value,
        available_cash=snapshot.available_cash,
    )
    summary = (
        "This is a rebalance-only run using live holdings and current market values. "
        "Fundamental analysis was skipped, so actions are based only on drift versus target weights. "
        "Review tax context and thesis quality before acting on any sell recommendation."
    )
    report = PortfolioReport(
        generated_at=datetime.now(UTC),
        portfolio_snapshot=snapshot,
        verdicts=[],
        portfolio_summary=summary,
        total_buy_required=sum(action.rupee_amount for action in actions if action.action == "BUY"),
        total_sell_required=sum(action.rupee_amount for action in actions if action.action == "SELL"),
        errors=[],
    )
    return report, actions


async def run_full_analysis(
    settings: Settings,
    event_callback: RunEventCallback | None = None,
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
    init_langfuse(settings)  # activate OTel provider before any @observe runs
    started = time.perf_counter()
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    skills_content = _build_analyst_prompt(settings)
    price_context_by_symbol: dict[str, dict[str, float | str]] = {}
    macro_context = ""
    macro_errors: list[str] = []
    budget = TokenBudgetManager(
        input_tokens_per_minute=settings.haiku_input_tpm,
        output_tokens_per_minute=settings.haiku_output_tpm,
    )

    if event_callback is not None and sync_result is None:
        event_callback(
            {"type": "phase", "phase": "kite_sync", "label": "Syncing live portfolio from Kite…", "total": 0}
        )

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
                    holding
                    for holding in equity_holdings
                    if _holding_requires_refresh(holding=holding, settings=settings)
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
            holdings_needing_context = [
                holding for holding in equity_holdings if _holding_requires_refresh(holding=holding, settings=settings)
            ]
            if holdings_needing_context:
                price_context_by_symbol = await _price_contexts(
                    settings=settings,
                    holdings=holdings_needing_context,
                )
            logger.info("Using saved same-day Kite snapshots; reusing snapshots but refreshing needed price history.")
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

    (macro_context, macro_errors), upcoming_results = await asyncio.gather(
        _build_macro_summary(),
        get_earnings_calendar(settings=settings),
    )

    weight_warnings = verify_portfolio_weights(
        sync_result.portfolio_snapshot.holdings,
        sync_result.portfolio_snapshot.total_value,
    )
    for warning in weight_warnings:
        logger.warning("[portfolio_weights] %s", warning)

    verdicts = await _run_analyst_fan_out(
        settings=settings,
        equity_holdings=equity_holdings,
        price_context_by_symbol=price_context_by_symbol,
        skills_content=skills_content,
        client=client,
        budget=budget,
        event_callback=event_callback,
    )

    if event_callback is not None:
        event_callback({"type": "phase", "phase": "rebalance", "label": "Computing rebalancing actions…", "total": 0})

    math_actions = calculate_rebalancing_actions(
        holdings=equity_holdings,
        total_value=sync_result.portfolio_snapshot.total_value,
        available_cash=sync_result.portfolio_snapshot.available_cash,
    )
    action_by_symbol = {action.tradingsymbol: action for action in math_actions}
    holding_by_symbol = {holding.tradingsymbol: holding for holding in equity_holdings}
    final_actions: dict[str, RebalancingAction] = {}
    errors = [verdict.error for verdict in verdicts if verdict.error]
    nonfatal_errors = list(macro_errors)
    if errors:
        first_error = next((verdict for verdict in verdicts if verdict.error), None)
        error_path = record_run_error(
            settings=settings,
            phase="analyst",
            error=errors[0],
            retries_used=0,
            ticker=first_error.tradingsymbol if first_error else None,
            partial_artifact_path=company_analysis_path(first_error.tradingsymbol, settings=settings)
            if first_error
            else None,
        )
        raise FullRunFailed(
            phase="analyst",
            message=errors[0],
            retries_used=0,
            ticker=first_error.tradingsymbol if first_error else None,
            error_log_path=error_path,
            partial_artifact_path=company_analysis_path(first_error.tradingsymbol, settings=settings)
            if first_error
            else None,
        )

    for verdict in verdicts:
        _merge_action_into_verdict(verdict, action_by_symbol.get(verdict.tradingsymbol))
        final_actions[verdict.tradingsymbol] = _verdict_to_action(verdict, holding_by_symbol[verdict.tradingsymbol])

    if event_callback is not None:
        event_callback({"type": "phase", "phase": "summary", "label": "Building portfolio summary…", "total": 0})

    try:
        portfolio_summary = await run_with_retries(
            lambda: _build_portfolio_summary(
                client=client,
                settings=settings,
                verdicts=verdicts,
                snapshot=sync_result.portfolio_snapshot,
                mf_symbols=[holding.tradingsymbol for holding in sync_result.mf_snapshot.holdings],
                errors=[error for error in [*errors, *nonfatal_errors] if error],
                macro_context=macro_context,
                upcoming_results=upcoming_results,
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

    total_buy_required = sum(verdict.rebalance_rupees for verdict in verdicts if verdict.rebalance_action == "BUY")
    total_sell_required = sum(verdict.rebalance_rupees for verdict in verdicts if verdict.rebalance_action == "SELL")
    elapsed = time.perf_counter() - started
    logger.info(
        "Full analysis completed in %.1fs across %s analyst sub-agents with parallelism=%s",
        elapsed,
        len(verdicts),
        settings.analyst_parallelism,
    )
    report = PortfolioReport(
        generated_at=sync_result.portfolio_snapshot.fetched_at,
        portfolio_snapshot=sync_result.portfolio_snapshot,
        verdicts=verdicts,
        portfolio_summary=portfolio_summary,
        total_buy_required=total_buy_required,
        total_sell_required=total_sell_required,
        errors=[error for error in [*errors, *nonfatal_errors] if error],
    )
    _save_run_manifest_safe(
        settings=settings,
        report=report,
        elapsed_seconds=elapsed,
        analyst_count=len(verdicts),
        snapshot_path=str(settings.kite_data_dir / "portfolio" / "latest_snapshot.json"),
        failure_reasons=[error for error in [*errors, *nonfatal_errors] if error],
    )
    return report


async def run_single_company_analysis(
    *,
    settings: Settings,
    ticker: str,
    exchange: str = "NSE",
) -> PortfolioReport:
    init_langfuse(settings)  # activate OTel provider before any @observe runs
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    skills_content = _build_analyst_prompt(settings)
    macro_context, macro_errors = await _build_macro_summary()
    snapshot: PortfolioSnapshot | None = None
    try:
        from persistence.store import load_latest_portfolio_snapshot

        snapshot = load_latest_portfolio_snapshot(settings)
    except FileNotFoundError:
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
        price_context = (await _price_contexts(settings=settings, holdings=[holding])).get(
            holding.tradingsymbol, price_context
        )

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
        fetched_at=snapshot.fetched_at if snapshot is not None else datetime.now(UTC),
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
        generated_at=datetime.now(UTC),
        portfolio_snapshot=report_snapshot,
        verdicts=[verdict],
        portfolio_summary=summary,
        total_buy_required=verdict.rebalance_rupees if verdict.rebalance_action == "BUY" else 0.0,
        total_sell_required=verdict.rebalance_rupees if verdict.rebalance_action == "SELL" else 0.0,
        errors=[error for error in [verdict.error, *macro_errors] if error],
    )
