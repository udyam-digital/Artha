from __future__ import annotations

import logging
from typing import Any

from models import AnalystReportCard, CompanyAnalysisArtifact, CompanyDataCard, Holding, StockVerdict

logger = logging.getLogger(__name__)


def _map_card_confidence(confidence: str) -> str:
    return confidence.upper()


def _map_card_verdict(verdict: str) -> str:
    mapping = {
        "BUY": "BUY",
        "ADD": "BUY",
        "HOLD": "HOLD",
        "TRIM": "SELL",
        "EXIT": "STRONG_SELL",
    }
    return mapping[verdict]


def _map_card_action(verdict: str) -> str:
    mapping = {
        "BUY": "BUY",
        "ADD": "BUY",
        "HOLD": "HOLD",
        "TRIM": "SELL",
        "EXIT": "SELL",
    }
    return mapping[verdict]


def _derive_bear_case(report_card: AnalystReportCard) -> str:
    risk_items = (
        report_card.risk_matrix.company_risks
        or report_card.risk_matrix.cyclical_risks
        or report_card.risk_matrix.structural_risks
    )
    base = risk_items[0] if risk_items else f"Risk level is {report_card.risk_matrix.risk_level.lower()}."
    governance = report_card.quality.governance_flags.strip()
    if governance and governance.lower() not in {"none", "nil", "no", "none identified"}:
        return f"{base} Governance watch: {governance}."
    return str(base)


def _derive_red_flags(report_card: AnalystReportCard) -> list[str]:
    flags = list(report_card.monitoring.red_flags)
    governance = report_card.quality.governance_flags.strip()
    if governance and governance.lower() not in {"none", "nil", "no", "none identified"}:
        flags.append(governance)
    deduped: list[str] = []
    seen: set[str] = set()
    for flag in flags:
        normalized = flag.strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped


def _report_card_to_stock_verdict(
    *,
    artifact: CompanyDataCard | CompanyAnalysisArtifact,
    holding: Holding,
    duration_seconds: float,
) -> StockVerdict:
    report_card = artifact.analysis if isinstance(artifact, CompanyDataCard) else artifact.report_card
    final_signal = report_card.final_verdict.verdict
    what_to_watch = (
        report_card.monitoring.key_metrics[0]
        if report_card.monitoring.key_metrics
        else report_card.monitoring.next_triggers[0]
        if report_card.monitoring.next_triggers
        else report_card.thesis.trigger
    )
    thesis_intact = final_signal != "EXIT"
    current_price = holding.last_price if holding.last_price > 0 else report_card.stock_snapshot.current_price
    buy_price = holding.average_price
    pnl_pct = holding.pnl_pct
    return StockVerdict(
        tradingsymbol=artifact.ticker.upper(),
        company_name=report_card.stock_snapshot.name,
        verdict=_map_card_verdict(final_signal),
        confidence=_map_card_confidence(report_card.final_verdict.confidence),
        current_price=current_price,
        buy_price=buy_price,
        pnl_pct=pnl_pct,
        thesis_intact=thesis_intact,
        bull_case=f"{report_card.thesis.core_idea} {report_card.thesis.growth_driver}".strip(),
        bear_case=_derive_bear_case(report_card),
        what_to_watch=what_to_watch,
        red_flags=_derive_red_flags(report_card),
        rebalance_action=_map_card_action(final_signal),
        rebalance_rupees=0.0,
        rebalance_reasoning=(
            f"Analyst report card verdict is {final_signal}, with timing {report_card.timing.timing_signal.lower()} "
            f"and risk level {report_card.risk_matrix.risk_level.lower()}."
        ),
        data_sources=report_card.data_sources,
        yfinance_data=artifact.yfinance_data if isinstance(artifact, CompanyAnalysisArtifact) else {},
        analysis_duration_seconds=duration_seconds,
        error=None,
    )


def _legacy_payload_to_stock_verdict(
    *,
    payload: dict[str, Any],
    holding: Holding,
    duration_seconds: float,
) -> StockVerdict:
    return StockVerdict.model_validate(
        {
            **payload,
            "tradingsymbol": str(payload.get("tradingsymbol", holding.tradingsymbol)).upper(),
            "company_name": str(payload.get("company_name", holding.tradingsymbol)),
            "current_price": payload.get("current_price", holding.last_price),
            "buy_price": payload.get("buy_price", holding.average_price),
            "pnl_pct": payload.get("pnl_pct", holding.pnl_pct),
            "analysis_duration_seconds": duration_seconds,
            "error": None,
        }
    )


def _build_fallback_verdict(
    *,
    holding: Holding,
    duration_seconds: float,
    error: str,
) -> StockVerdict:
    logger.error("[%s] analyst fallback: %s", holding.tradingsymbol, error)
    return StockVerdict(
        tradingsymbol=holding.tradingsymbol,
        company_name=holding.tradingsymbol,
        verdict="HOLD",
        confidence="LOW",
        current_price=holding.last_price,
        buy_price=holding.average_price,
        pnl_pct=holding.pnl_pct,
        thesis_intact=False,
        bull_case="Insufficient verified analyst output to support a positive thesis.",
        bear_case="The stock requires manual review because the automated analyst run failed.",
        what_to_watch="Re-run analysis after fixing the underlying error.",
        red_flags=[],
        rebalance_action="HOLD",
        rebalance_rupees=0.0,
        rebalance_reasoning="Analyst failed, so no action is taken automatically.",
        data_sources=[],
        yfinance_data={},
        analysis_duration_seconds=duration_seconds,
        error=error,
    )
