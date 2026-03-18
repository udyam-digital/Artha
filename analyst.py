from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from anthropic import AsyncAnthropic

from config import Settings
from models import AnalystReportCard, CompanyAnalysisArtifact, Holding, StockVerdict
from snapshot_store import save_company_analysis_artifact
from tools import get_web_search_tool_definition


logger = logging.getLogger(__name__)

MAX_ANALYST_ITERATIONS = 6
JSON_PARSE_REPAIR_PROMPT = (
    "Your previous response was not valid JSON for the required analyst report card schema. "
    "Return exactly one valid JSON object only. Do not include markdown, prose, XML, code fences, or explanations."
)


def _extract_text(response: Any) -> str:
    text_parts: list[str] = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "text":
            text_parts.append(getattr(block, "text", ""))
    return "\n".join(text_parts).strip()


def _extract_report_card_dict(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    if not text:
        raise ValueError("Analyst response was empty.")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = _extract_first_json_object(text)
    if not isinstance(payload, dict):
        raise ValueError("Analyst response was not a JSON object.")
    return payload


def _extract_first_json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    preview = text[:240].replace("\n", "\\n")
    raise ValueError(f"Analyst response did not contain a valid JSON object. Preview: {preview}")


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
    artifact: CompanyAnalysisArtifact,
    holding: Holding,
    duration_seconds: float,
) -> StockVerdict:
    report_card = artifact.report_card
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
        analysis_duration_seconds=duration_seconds,
        error=error,
    )


def _materialize_tool_results(response: Any) -> list[dict[str, Any]]:
    tool_results: list[dict[str, Any]] = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) != "tool_use":
            continue
        # Native web_search is a server tool; this placeholder keeps the loop
        # compatible with test doubles that expect a tool_result round-trip.
        tool_results.append(
            {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(
                    {
                        "acknowledged": True,
                        "tool_name": getattr(block, "name", "web_search"),
                        "tool_input": getattr(block, "input", {}),
                    },
                    ensure_ascii=True,
                ),
            }
        )
    return tool_results


def _log_response_usage(*, label: str, response: Any) -> None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    logger.info(
        "%s token usage: input=%s output=%s",
        label,
        getattr(usage, "input_tokens", "unknown"),
        getattr(usage, "output_tokens", "unknown"),
    )


def _build_company_artifact(
    *,
    report_card: AnalystReportCard,
    holding: Holding,
    config: Settings,
) -> CompanyAnalysisArtifact:
    return CompanyAnalysisArtifact(
        generated_at=datetime.now(timezone.utc),
        source_model=config.analyst_model,
        exchange=holding.exchange,
        ticker=holding.tradingsymbol.upper(),
        report_card=report_card,
    )


async def generate_company_artifact(
    holding: Holding,
    price_context: dict[str, float | str],
    skills_content: str,
    client: AsyncAnthropic,
    config: Settings,
) -> CompanyAnalysisArtifact:
    started = time.perf_counter()
    logger.info("[%s] starting analysis", holding.tradingsymbol)

    has_portfolio_context = any(
        (
            holding.quantity,
            holding.average_price,
            holding.current_value,
            holding.current_weight_pct,
            holding.target_weight_pct,
            holding.pnl,
            holding.pnl_pct,
        )
    )
    portfolio_context = ""
    if has_portfolio_context:
        portfolio_context = f"""
    Portfolio context:
    Quantity held: {holding.quantity}
    Average buy price: ₹{holding.average_price}
    Current price: ₹{holding.last_price}
    Current value: ₹{holding.current_value}
    P&L: ₹{holding.pnl} ({holding.pnl_pct:.1f}%)
    Current portfolio weight: {holding.current_weight_pct:.1f}%
    Target portfolio weight: {holding.target_weight_pct:.1f}%
    Drift from target: {holding.current_weight_pct - holding.target_weight_pct:+.1f}%
"""

    user_prompt = f"""
    Analyse this stock for Saksham:

    Stock: {holding.tradingsymbol}
    Exchange: {holding.exchange}
{portfolio_context}
    52-week context:
    52w High: ₹{price_context.get('52w_high', 'N/A')}
    52w Low: ₹{price_context.get('52w_low', 'N/A')}
    Current vs 52w High: {price_context.get('current_vs_52w_high_pct', 'N/A')}%

    If portfolio context is missing, treat this as a standalone unbiased research request.
    Research this stock using web search and return a valid JSON object only.
    """

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]

    for iteration in range(1, MAX_ANALYST_ITERATIONS + 1):
        response = await client.messages.create(
            model=config.analyst_model,
            max_tokens=config.analyst_max_tokens,
            system=skills_content,
            messages=messages,
            tools=[get_web_search_tool_definition()],
        )
        _log_response_usage(label=f"[{holding.tradingsymbol}] analyst", response=response)
        stop_reason = getattr(response, "stop_reason", None)

        if stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue

        if stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = _materialize_tool_results(response)
            if tool_results:
                messages.append({"role": "user", "content": tool_results})
            continue

        if stop_reason in {"end_turn", "max_tokens"}:
            raw_text = _extract_text(response)
            try:
                payload = _extract_report_card_dict(raw_text)
                report_card = AnalystReportCard.model_validate(payload)
            except Exception as exc:
                logger.warning(
                    "[%s] invalid analyst JSON on iteration %s/%s: %s",
                    holding.tradingsymbol,
                    iteration,
                    MAX_ANALYST_ITERATIONS,
                    exc,
                )
                if iteration >= MAX_ANALYST_ITERATIONS:
                    raise
                messages.append({"role": "assistant", "content": response.content})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"{JSON_PARSE_REPAIR_PROMPT} Validation error: {exc}. "
                            "Keep all required keys and enum values exactly as specified."
                        ),
                    }
                )
                continue

            artifact = _build_company_artifact(report_card=report_card, holding=holding, config=config)
            output_path = save_company_analysis_artifact(artifact, settings=config)
            logger.info(
                "[%s] saved company analysis to %s in %.1fs",
                holding.tradingsymbol,
                output_path,
                time.perf_counter() - started,
            )
            return artifact

        raise ValueError(f"Unexpected stop_reason: {stop_reason}")

    raise ValueError("Analyst sub-agent exceeded MAX_ANALYST_ITERATIONS.")


async def analyse_stock(
    holding: Holding,
    portfolio_total_value: float,
    price_context: dict[str, float | str],
    skills_content: str,
    client: AsyncAnthropic,
    config: Settings,
) -> StockVerdict:
    """
    Single-stock sub-agent. One focused Claude API call with web search.
    Returns StockVerdict. Never raises — returns StockVerdict with error field.
    """
    del portfolio_total_value
    started = time.perf_counter()
    try:
        artifact = await generate_company_artifact(
            holding=holding,
            price_context=price_context,
            skills_content=skills_content,
            client=client,
            config=config,
        )
        verdict = _report_card_to_stock_verdict(
            artifact=artifact,
            holding=holding,
            duration_seconds=time.perf_counter() - started,
        )
        logger.info(
            "[%s] done in %.1fs — %s",
            holding.tradingsymbol,
            verdict.analysis_duration_seconds,
            verdict.verdict,
        )
        return verdict
    except Exception as exc:
        duration_seconds = time.perf_counter() - started
        verdict = _build_fallback_verdict(
            holding=holding,
            duration_seconds=duration_seconds,
            error=str(exc),
        )
        logger.info(
            "[%s] done in %.1fs — %s",
            holding.tradingsymbol,
            verdict.analysis_duration_seconds,
            verdict.verdict,
        )
        return verdict
