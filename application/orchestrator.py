from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypedDict

from anthropic import AsyncAnthropic

from analysis.company import get_company_artifact_and_verdict, is_company_artifact_fresh
from analysis.verify import verify_portfolio_weights
from config import Settings
from kite.runtime import KiteSyncResult, build_kite_client, sync_kite_data_with_client
from kite.tools import ToolExecutionError, get_macro_context, kite_get_price_history
from models import Holding, MacroContext, PortfolioReport, PortfolioSnapshot, RebalancingAction, StockVerdict, Verdict
from observability.langfuse_client import init_langfuse
from observability.token_budget import TokenBudgetManager
from observability.usage import (
    count_input_tokens_exact,
    log_estimated_input_tokens,
    record_anthropic_usage,
    record_run_error,
)
from persistence.store import company_analysis_path, load_company_analysis_artifact, save_run_manifest
from rebalance import PASSIVE_INSTRUMENTS, calculate_rebalancing_actions
from reliability import FullRunFailed, RetryFailure, run_with_retries


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


logger = logging.getLogger(__name__)


def _format_macro_value(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2f}"


async def _build_macro_summary() -> tuple[str, list[str]]:
    try:
        macro = await get_macro_context()
    except Exception:
        macro = MacroContext(fetch_errors=["macro_context: failed to initialize"])
    errors = list(macro.fetch_errors)
    if macro.cpi_headline_yoy is None and macro.iip_growth_latest is None and macro.gdp_growth_latest is None:
        return "", errors
    as_of_date = macro.as_of_date or "unknown"
    summary = (
        f"Macro (as of {as_of_date}): CPI {_format_macro_value(macro.cpi_headline_yoy)}% | "
        f"IIP growth {_format_macro_value(macro.iip_growth_latest)}% | "
        f"GDP growth {_format_macro_value(macro.gdp_growth_latest)}%"
    )
    return summary, errors


async def _log_portfolio_summary_input_tokens(
    *,
    client: AsyncAnthropic,
    settings: Settings,
    messages: list[dict[str, str]],
) -> None:
    try:
        exact = await count_input_tokens_exact(
            client=client,
            model=settings.model,
            messages=messages,
        )
    except Exception as exc:
        logger.warning("[portfolio_summary] exact token counting failed; falling back to estimate: %s", exc)
        log_estimated_input_tokens(label="[portfolio_summary]", messages=messages)
        return
    logger.info("[portfolio_summary] exact input tokens: %s", exact)


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


def _build_analyst_prompt(settings: Settings) -> str:
    """Load analyst_prompt.md and inject fiscal + search context variables."""
    from analysis.fiscal import get_fiscal_context

    template = (Path("skills") / "analyst_prompt.md").read_text(encoding="utf-8")
    ctx = get_fiscal_context()
    ctx["max_searches"] = str(settings.analyst_max_searches)
    for key, value in ctx.items():
        template = template.replace(f"${{{key}}}", value)
    return template


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
        and is_company_artifact_fresh(artifact=cached, settings=settings, current_price=holding.last_price)
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
    await _log_portfolio_summary_input_tokens(client=client, settings=settings, messages=messages)
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

    macro_context, macro_errors = await _build_macro_summary()

    weight_warnings = verify_portfolio_weights(
        sync_result.portfolio_snapshot.holdings,
        sync_result.portfolio_snapshot.total_value,
    )
    for warning in weight_warnings:
        logger.warning("[portfolio_weights] %s", warning)

    semaphore = asyncio.Semaphore(settings.analyst_parallelism)

    async def bounded_analyse(holding: Holding, index: int) -> StockVerdict:
        stagger_seconds = max(settings.analyst_min_start_interval_seconds, 0.0)
        if stagger_seconds > 0:
            await asyncio.sleep(index * stagger_seconds)
        async with semaphore:
            await budget.acquire(estimated_input_tokens=4000, estimated_output_tokens=1500)
            _, verdict, from_cache = await run_with_retries(
                lambda: get_company_artifact_and_verdict(
                    holding=holding,
                    price_context=price_context_by_symbol.get(holding.tradingsymbol, _default_price_context()),
                    macro_context=macro_context,
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

    if event_callback is not None:
        event_callback(
            {
                "type": "phase",
                "phase": "analyst",
                "label": f"Analysing {len(task_to_symbol)} holding(s)…",
                "total": len(task_to_symbol),
            }
        )

    ordered_verdicts: dict[str, StockVerdict] = {}
    completed = 0
    total = len(task_to_symbol)
    for task in asyncio.as_completed(task_to_symbol):
        try:
            verdict = await task
        except RetryFailure as exc:
            pending_tasks = [active_task for active_task in task_to_symbol if not active_task.done()]
            for active_task in pending_tasks:
                active_task.cancel()
            if pending_tasks:
                await asyncio.gather(*pending_tasks, return_exceptions=True)
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
        if event_callback is not None:
            event_callback(
                {
                    "type": "analyst_complete",
                    "completed": completed,
                    "total": total,
                    "ticker": verdict.tradingsymbol,
                    "verdict": verdict.verdict.value,
                    "confidence": verdict.confidence,
                    "thesis_intact": verdict.thesis_intact,
                    "pnl_pct": verdict.pnl_pct,
                    "duration_seconds": verdict.analysis_duration_seconds,
                    "bull_case": verdict.bull_case,
                    "red_flags": verdict.red_flags,
                }
            )

    verdicts = [
        ordered_verdicts[holding.tradingsymbol]
        for holding in equity_holdings
        if holding.tradingsymbol in ordered_verdicts
    ]

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


def _save_run_manifest_safe(
    *,
    settings: Settings,
    report: PortfolioReport,
    elapsed_seconds: float,
    analyst_count: int,
    snapshot_path: str,
    failure_reasons: list[str],
) -> None:
    try:
        verdict_counts: dict[str, int] = {"BUY": 0, "HOLD": 0, "SELL": 0}
        for v in report.verdicts:
            val = v.verdict.value if hasattr(v.verdict, "value") else str(v.verdict)
            if val in {"STRONG_BUY", "BUY"}:
                verdict_counts["BUY"] += 1
            elif val in {"STRONG_SELL", "SELL"}:
                verdict_counts["SELL"] += 1
            else:
                verdict_counts["HOLD"] += 1
        run_id = report.generated_at.strftime("%Y%m%d_%H%M%S")
        manifest = {
            "run_id": run_id,
            "generated_at": report.generated_at.isoformat(),
            "snapshot_paths_used": [snapshot_path],
            "analyst_inputs": analyst_count,
            "elapsed_seconds": round(elapsed_seconds, 2),
            "verdict_counts": verdict_counts,
            "error_count": len(report.errors),
            "failure_reasons": failure_reasons,
        }
        save_run_manifest(manifest, settings.reports_dir)
    except Exception:
        logger.warning("Failed to save run manifest; continuing", exc_info=True)


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
        macro_context=macro_context,
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
