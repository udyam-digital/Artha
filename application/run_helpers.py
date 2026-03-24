from __future__ import annotations

import asyncio
import logging
from typing import Any

from analysis.company import get_company_artifact_and_verdict, is_company_artifact_fresh
from application.context_builders import _default_price_context
from application.events import RunEventCallback
from config import Settings
from models import Holding, PortfolioReport, StockVerdict
from observability.token_budget import TokenBudgetManager
from observability.usage import record_run_error
from persistence.store import company_analysis_path, load_company_analysis_artifact, save_run_manifest
from reliability import FullRunFailed, RetryFailure, run_with_retries

logger = logging.getLogger(__name__)


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


async def _run_analyst_fan_out(
    *,
    settings: Settings,
    equity_holdings: list[Holding],
    price_context_by_symbol: dict[str, dict[str, Any]],
    skills_content: str,
    client: Any,
    budget: TokenBudgetManager,
    event_callback: RunEventCallback | None,
) -> list[StockVerdict]:
    """Run per-holding analyst tasks in parallel and return verdicts in holding order."""
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

    task_to_symbol: dict[asyncio.Task[StockVerdict], str] = {
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

    return [
        ordered_verdicts[holding.tradingsymbol]
        for holding in equity_holdings
        if holding.tradingsymbol in ordered_verdicts
    ]
