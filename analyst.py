from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic

from config import Settings
from models import Holding, StockVerdict
from tools import get_web_search_tool_definition


logger = logging.getLogger(__name__)

MAX_ANALYST_ITERATIONS = 6


def _extract_text(response: Any) -> str:
    text_parts: list[str] = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "text":
            text_parts.append(getattr(block, "text", ""))
    return "\n".join(text_parts).strip()


def _extract_tagged_json(raw_text: str) -> dict[str, Any]:
    match = re.search(r"<verdict>\s*(\{.*\})\s*</verdict>", raw_text, re.DOTALL)
    if not match:
        raise ValueError("Analyst response did not contain <verdict> JSON tags.")
    payload = json.loads(match.group(1))
    if not isinstance(payload, dict):
        raise ValueError("Analyst response was not a JSON object.")
    return payload


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
    Research this stock using web search and return your verdict.
    """

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]
    raw_text = ""

    try:
        for _ in range(MAX_ANALYST_ITERATIONS):
            response = await client.messages.create(
                model=config.model,
                max_tokens=config.max_tokens,
                system=skills_content,
                messages=messages,
                tools=[get_web_search_tool_definition()],
            )
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
                payload = _extract_tagged_json(raw_text)
                verdict = StockVerdict.model_validate(
                    {
                        **payload,
                        "tradingsymbol": str(payload.get("tradingsymbol", holding.tradingsymbol)).upper(),
                        "company_name": str(payload.get("company_name", holding.tradingsymbol)),
                        "current_price": holding.last_price,
                        "buy_price": holding.average_price,
                        "pnl_pct": holding.pnl_pct,
                        "analysis_duration_seconds": time.perf_counter() - started,
                        "error": None,
                    }
                )
                logger.info(
                    "[%s] done in %.1fs — %s",
                    holding.tradingsymbol,
                    verdict.analysis_duration_seconds,
                    verdict.verdict,
                )
                return verdict

            raise ValueError(f"Unexpected stop_reason: {stop_reason}")

        raise ValueError("Analyst sub-agent exceeded MAX_ANALYST_ITERATIONS.")
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
