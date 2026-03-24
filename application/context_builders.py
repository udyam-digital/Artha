from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from anthropic import AsyncAnthropic

from config import Settings
from kite.runtime import build_kite_client
from kite.tools import ToolExecutionError, get_macro_context, kite_get_price_history
from models import Holding, MacroContext, PortfolioSnapshot, StockVerdict
from observability.usage import (
    count_input_tokens_exact,
    log_estimated_input_tokens,
    record_anthropic_usage,
)
from reliability import RetryFailure, run_with_retries

logger = logging.getLogger(__name__)


def _format_macro_value(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2f}"


async def _build_macro_summary() -> tuple[str, list[str]]:
    try:
        macro = await get_macro_context()
    except Exception:
        macro = MacroContext(fetch_errors=["macro_context: failed to initialize"])
    errors = list(macro.fetch_errors)
    has_data = any(
        v is not None
        for v in (
            macro.cpi_headline_yoy,
            macro.iip_growth_latest,
            macro.iip_capital_goods_growth,
            macro.gdp_growth_latest,
        )
    )
    if not has_data:
        return "", errors

    parts = []
    if macro.cpi_headline_yoy is not None:
        label = f"as of {macro.cpi_as_of}" if macro.cpi_as_of else ""
        parts.append(f"CPI inflation {_format_macro_value(macro.cpi_headline_yoy)}% YoY ({label})")
    if macro.iip_growth_latest is not None:
        label = macro.iip_as_of or ""
        parts.append(f"IIP General growth {_format_macro_value(macro.iip_growth_latest)}% ({label})")
    if macro.iip_capital_goods_growth is not None:
        parts.append(f"IIP Capital Goods growth {_format_macro_value(macro.iip_capital_goods_growth)}%")
    if macro.gdp_growth_latest is not None:
        label = f"as of {macro.gdp_as_of}" if macro.gdp_as_of else ""
        parts.append(f"GDP growth {_format_macro_value(macro.gdp_growth_latest)}% at constant prices ({label})")
    if macro.unemployment_rate is not None:
        label = macro.unemployment_as_of or ""
        parts.append(f"Unemployment rate {_format_macro_value(macro.unemployment_rate)}% ({label})")

    summary = "India Macro Context: " + " | ".join(parts)
    return summary, errors


def _build_analyst_prompt(settings: Settings) -> str:
    """Load analyst_prompt.md and inject fiscal + search context variables."""
    from analysis.fiscal import get_fiscal_context

    template = (Path("skills") / "analyst_prompt.md").read_text(encoding="utf-8")
    ctx = get_fiscal_context()
    ctx["max_searches"] = str(settings.analyst_max_searches)
    for key, value in ctx.items():
        template = template.replace(f"${{{key}}}", value)
    return template


def _default_price_context() -> dict[str, float]:
    return {
        "52w_high": 0.0,
        "52w_low": 0.0,
        "current_vs_52w_high_pct": 0.0,
        "price_1y_ago": 0.0,
        "price_change_1y_pct": 0.0,
    }


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


async def _build_portfolio_summary(
    *,
    client: AsyncAnthropic,
    settings: Settings,
    verdicts: list[StockVerdict],
    snapshot: PortfolioSnapshot,
    mf_symbols: list[str],
    errors: list[str],
    macro_context: str = "",
    upcoming_results: list[dict] | None = None,
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
    macro_line = f"\nIndia Macro Context: {macro_context}" if macro_context else ""
    calendar_line = ""
    if upcoming_results:
        items = [f"{r.get('company', '?')} ({r.get('result_date', '?')})" for r in upcoming_results[:10]]
        calendar_line = f"\nUpcoming results (next 21 days): {', '.join(items)}"
    messages = [
        {
            "role": "user",
            "content": (
                f"Write a concise 3-5 sentence summary for Saksham's Indian equity {subject}. "
                "Do not redo analysis. Use only the supplied verdict JSON and mention the main concentration, "
                f"risk, and rebalance takeaways.{macro_line}{calendar_line}\n"
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
