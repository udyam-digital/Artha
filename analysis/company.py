from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from collections.abc import Awaitable, Callable

from anthropic import AsyncAnthropic

from analysis.analyst import generate_company_artifact
from config import Settings
from models import CompanyAnalysisArtifact, Holding, StockVerdict
from persistence.store import load_company_analysis_artifact


logger = logging.getLogger(__name__)


def artifact_to_stock_verdict(
    *,
    artifact: CompanyAnalysisArtifact,
    holding: Holding,
    duration_seconds: float,
) -> StockVerdict:
    report_card = artifact.report_card
    final_signal = report_card.final_verdict.verdict
    risk_items = (
        report_card.risk_matrix.company_risks
        or report_card.risk_matrix.cyclical_risks
        or report_card.risk_matrix.structural_risks
    )
    base_bear = risk_items[0] if risk_items else f"Risk level is {report_card.risk_matrix.risk_level.lower()}."
    governance = report_card.quality.governance_flags.strip()
    bear_case = (
        f"{base_bear} Governance watch: {governance}."
        if governance and governance.lower() not in {"none", "nil", "no", "none identified"}
        else str(base_bear)
    )
    red_flags = list(report_card.monitoring.red_flags)
    if governance and governance.lower() not in {"none", "nil", "no", "none identified"}:
        red_flags.append(governance)

    deduped_flags: list[str] = []
    seen: set[str] = set()
    for flag in red_flags:
        normalized = flag.strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped_flags.append(normalized)

    verdict_map = {
        "BUY": "BUY",
        "ADD": "BUY",
        "HOLD": "HOLD",
        "TRIM": "SELL",
        "EXIT": "STRONG_SELL",
    }
    action_map = {
        "BUY": "BUY",
        "ADD": "BUY",
        "HOLD": "HOLD",
        "TRIM": "SELL",
        "EXIT": "SELL",
    }
    what_to_watch = (
        report_card.monitoring.key_metrics[0]
        if report_card.monitoring.key_metrics
        else report_card.monitoring.next_triggers[0]
        if report_card.monitoring.next_triggers
        else report_card.thesis.trigger
    )

    current_price = holding.last_price if holding.last_price > 0 else report_card.stock_snapshot.current_price
    return StockVerdict(
        tradingsymbol=artifact.ticker.upper(),
        company_name=report_card.stock_snapshot.name,
        verdict=verdict_map[final_signal],
        confidence=report_card.final_verdict.confidence.upper(),
        current_price=current_price,
        buy_price=holding.average_price,
        pnl_pct=holding.pnl_pct,
        thesis_intact=final_signal != "EXIT",
        bull_case=f"{report_card.thesis.core_idea} {report_card.thesis.growth_driver}".strip(),
        bear_case=bear_case,
        what_to_watch=what_to_watch,
        red_flags=deduped_flags,
        rebalance_action=action_map[final_signal],
        rebalance_rupees=0.0,
        rebalance_reasoning=(
            f"Analyst report card verdict is {final_signal}, with timing "
            f"{report_card.timing.timing_signal.lower()} and risk level "
            f"{report_card.risk_matrix.risk_level.lower()}."
        ),
        data_sources=report_card.data_sources,
        analysis_duration_seconds=duration_seconds,
        error=None,
    )


def is_company_artifact_fresh(*, artifact: CompanyAnalysisArtifact, settings: Settings) -> bool:
    max_age = timedelta(days=settings.company_analysis_max_age_days)
    age = datetime.now(timezone.utc) - artifact.generated_at
    return age <= max_age


async def get_company_artifact_and_verdict(
    *,
    holding: Holding,
    price_context: dict[str, float | str],
    skills_content: str,
    client: AsyncAnthropic,
    settings: Settings,
    before_generate: Callable[[], Awaitable[None]] | None = None,
) -> tuple[CompanyAnalysisArtifact, StockVerdict, bool]:
    artifact: CompanyAnalysisArtifact | None = None
    from_cache = False

    try:
        cached = load_company_analysis_artifact(holding.tradingsymbol, settings=settings)
        if cached.ticker.upper() == holding.tradingsymbol.upper() and is_company_artifact_fresh(
            artifact=cached,
            settings=settings,
        ):
            artifact = cached
            from_cache = True
            logger.info("[%s] reusing fresh company analysis cache", holding.tradingsymbol)
        else:
            logger.info("[%s] company analysis cache is stale; refreshing", holding.tradingsymbol)
    except FileNotFoundError:
        logger.info("[%s] no company analysis cache found", holding.tradingsymbol)
    except Exception:
        logger.warning("[%s] invalid company analysis cache; refreshing", holding.tradingsymbol, exc_info=True)

    if artifact is None:
        if before_generate is not None:
            await before_generate()
        started = time.perf_counter()
        artifact = await generate_company_artifact(
            holding=holding,
            price_context=price_context,
            skills_content=skills_content,
            client=client,
            config=settings,
        )
        duration_seconds = time.perf_counter() - started
    else:
        duration_seconds = 0.0

    verdict = artifact_to_stock_verdict(
        artifact=artifact,
        holding=holding,
        duration_seconds=duration_seconds,
    )
    return artifact, verdict, from_cache
