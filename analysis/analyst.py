from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import instructor
from anthropic import AsyncAnthropic

from analysis.data_card import build_company_data_card
from analysis.fiscal import get_fiscal_context
from analysis.judge import judge_factual_grounding, judge_report_card
from config import Settings
from kite.tools import (
    get_nse_india_provider_payload,
    get_yfinance_company_info,
    get_yfinance_provider_payload,
    get_yfinance_snapshot,
)
from models import (
    AnalystInputPayload,
    AnalystReportCard,
    CompanyAnalysisArtifact,
    CompanyDataCard,
    Holding,
    StockVerdict,
)
from observability.langfuse_client import get_langfuse, score_active_trace
from observability.usage import (
    count_input_tokens_exact,
    estimate_input_tokens,
    log_estimated_input_tokens,
    record_anthropic_usage,
)
from persistence.store import save_company_analysis_artifact, save_judge_scores
from providers.tavily import DEFAULT_TAVILY_MAX_RESULTS, get_tavily_search_tool_definition, tavily_search

logger = logging.getLogger(__name__)

MAX_ANALYST_ITERATIONS = 6


def _make_instructor_client(api_key: str) -> instructor.AsyncInstructor:
    return instructor.from_anthropic(
        AsyncAnthropic(api_key=api_key),
        mode=instructor.Mode.ANTHROPIC_JSON,
    )


def _extract_text(response: Any) -> str:
    text_parts: list[str] = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "text":
            text_parts.append(getattr(block, "text", ""))
    return "\n".join(text_parts).strip()


def _serialize_content_blocks(blocks: list[Any]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for block in blocks:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            serialized.append({"type": "text", "text": getattr(block, "text", "")})
            continue
        if block_type == "tool_use":
            serialized.append(
                {
                    "type": "tool_use",
                    "id": getattr(block, "id", ""),
                    "name": getattr(block, "name", ""),
                    "input": getattr(block, "input", {}) or {},
                }
            )
            continue
        serialized.append({"type": str(block_type or "unknown")})
    return serialized


def _ensure_instructor_client(client: Any, *, api_key: str) -> Any:
    messages = getattr(client, "messages", None)
    if messages is not None and hasattr(messages, "create_with_completion"):
        return client
    if isinstance(client, AsyncAnthropic):
        return instructor.from_anthropic(client, mode=instructor.Mode.ANTHROPIC_JSON)
    if messages is not None and hasattr(messages, "create"):
        return client
    return _make_instructor_client(api_key)


def _raw_client_for_counting(client: Any) -> AsyncAnthropic | Any:
    return getattr(client, "client", client)


async def _log_input_tokens(
    *,
    label: str,
    client: Any,
    model: str,
    messages: list[dict[str, Any]],
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> None:
    try:
        exact = await count_input_tokens_exact(
            client=_raw_client_for_counting(client),
            model=model,
            messages=messages,
            system=system,
            tools=tools,
        )
    except Exception as exc:
        logger.warning("%s exact token counting failed; falling back to estimate: %s", label, exc)
        log_estimated_input_tokens(label=label, messages=messages, system=system)
        return
    logger.info("%s exact input tokens: %s", label, exact)


_SOURCE_MAP_KEY_ALIASES: dict[str, str] = {
    # revenue_cagr aliases
    "revenue": "revenue_cagr",
    "revenue_growth": "revenue_cagr",
    "revenue_yoy": "revenue_cagr",
    "q3_fy26_revenue": "revenue_cagr",
    "q2_fy26_revenue": "revenue_cagr",
    "q4_fy26_revenue": "revenue_cagr",
    "q3_revenue": "revenue_cagr",
    "revenue_cagr_source": "revenue_cagr",
    # eps_cagr aliases
    "eps": "eps_cagr",
    "eps_growth": "eps_cagr",
    "net_profit": "eps_cagr",
    "q3_fy26_netprofit": "eps_cagr",
    "q3_diluted_eps": "eps_cagr",
    "eps_cagr_source": "eps_cagr",
    "q3_fy26_eps": "eps_cagr",
    "netprofit": "eps_cagr",
    # roce aliases
    "roce_source": "roce",
    "return_on_capital": "roce",
    # roe aliases
    "roe_source": "roe",
    "return_on_equity": "roe",
    # pe aliases
    "pe_ratio": "pe",
    "pe_source": "pe",
    "trailing_pe": "pe",
    # peg aliases
    "peg_ratio": "peg",
    "peg_source": "peg",
    # fcf_yield aliases
    "fcf": "fcf_yield",
    "free_cash_flow": "fcf_yield",
    "fcf_yield_source": "fcf_yield",
    # debt_to_equity aliases
    "de_ratio": "debt_to_equity",
    "debt_equity": "debt_to_equity",
    "d_e_ratio": "debt_to_equity",
    "debt_to_equity_source": "debt_to_equity",
    # fair_value aliases
    "fair_value_range": "fair_value",
    "fair_value_source": "fair_value",
    "valuation": "fair_value",
    # risk_1 aliases
    "risk": "risk_1",
    "primary_risk": "risk_1",
    "risk_1_source": "risk_1",
    # analyst_target aliases
    "target_price": "analyst_target",
    "consensus_target": "analyst_target",
    # market_share aliases
    "market_position": "market_share",
    "competitive_position": "market_share",
}

REQUIRED_SOURCE_MAP_KEYS = [
    "revenue_cagr",
    "eps_cagr",
    "roce",
    "roe",
    "pe",
    "peg",
    "fcf_yield",
    "debt_to_equity",
    "fair_value",
    "risk_1",
    "analyst_target",
    "market_share",
]


def _is_valid_source_map_value(value: str) -> bool:
    """Check if a source_map value is a URL, 'Not available', or a known API provider."""
    v = value.strip()
    return v.startswith("http") or v.lower() in ("not available", "yfinance api", "nse india api")


def _normalize_source_map_keys(source_map: dict[str, str]) -> dict[str, str]:
    """Normalize LLM-generated source_map keys to the 12 required standard keys.
    Also filters out data values (non-URLs) that the LLM sometimes puts in source_map."""
    normalized: dict[str, str] = {}
    # First pass: copy entries with standard keys (only if value is URL or "Not available")
    for key, value in source_map.items():
        if not _is_valid_source_map_value(value):
            continue  # Skip data values like "₹319 cr, +20% YoY"
        lower_key = key.lower().strip()
        if lower_key in REQUIRED_SOURCE_MAP_KEYS:
            if lower_key not in normalized or normalized[lower_key] == "Not available":
                normalized[lower_key] = value
        else:
            # Try alias mapping
            mapped = _SOURCE_MAP_KEY_ALIASES.get(lower_key)
            if mapped and (mapped not in normalized or normalized[mapped] == "Not available"):
                normalized[mapped] = value
    # Ensure all 12 required keys exist
    for key in REQUIRED_SOURCE_MAP_KEYS:
        if key not in normalized:
            normalized[key] = "Not available"
    return normalized


def _extract_source_map_from_raw(raw_text: str) -> dict[str, str]:
    """Extract source_map from raw LLM JSON text before instructor coercion can drop it."""
    try:
        parsed = json.loads(raw_text)
        sm = parsed.get("source_map", {})
        if isinstance(sm, dict):
            return {str(k): str(v) for k, v in sm.items()}
    except (json.JSONDecodeError, Exception):
        pass
    # Fallback: try to find source_map in partial JSON
    import re

    match = re.search(r'"source_map"\s*:\s*\{([^}]+)\}', raw_text, re.DOTALL)
    if match:
        try:
            sm = json.loads("{" + match.group(1) + "}")
            return {str(k): str(v) for k, v in sm.items()}
        except (json.JSONDecodeError, Exception):
            pass
    return {}


def _extract_data_sources_from_raw(raw_text: str) -> list[str]:
    """Extract data_sources from raw LLM JSON text as backup."""
    try:
        parsed = json.loads(raw_text)
        ds = parsed.get("data_sources", [])
        if isinstance(ds, list):
            return [str(u) for u in ds if str(u).startswith("http")]
    except (json.JSONDecodeError, Exception):
        pass
    return []


async def _coerce_report_card_with_instructor(
    *,
    instructor_client: Any,
    config: Settings,
    holding: Holding,
    raw_text: str,
) -> tuple[AnalystReportCard, Any]:
    # Pre-extract source_map and data_sources from raw text before instructor may drop them
    raw_source_map = _extract_source_map_from_raw(raw_text)
    raw_data_sources = _extract_data_sources_from_raw(raw_text)

    messages = [
        {
            "role": "user",
            "content": (
                "Convert the following stock analysis draft into a valid AnalystReportCard JSON object. "
                "Use only facts present in the draft. Do not add commentary outside the schema. "
                "IMPORTANT: Preserve all data_sources URLs and source_map entries from the draft exactly as written.\n"
                f"Ticker: {holding.tradingsymbol}\n"
                f"Draft:\n{raw_text}"
            ),
        }
    ]
    await _log_input_tokens(
        label=f"[{holding.tradingsymbol}] [structured]",
        client=instructor_client,
        model=config.analyst_model,
        messages=messages,
    )
    if hasattr(instructor_client.messages, "create_with_completion"):
        report_card, completion = await instructor_client.messages.create_with_completion(
            response_model=AnalystReportCard,
            model=config.analyst_model,
            max_tokens=config.analyst_max_tokens,
            messages=messages,
        )
        # Re-inject source_map if instructor dropped it or lost entries
        if raw_source_map:
            if not report_card.source_map:
                report_card.source_map = raw_source_map
                logger.info(
                    "[%s] re-injected source_map (%d entries) from raw text", holding.tradingsymbol, len(raw_source_map)
                )
            else:
                # Merge any missing keys from raw
                for k, v in raw_source_map.items():
                    if k not in report_card.source_map:
                        report_card.source_map[k] = v
        # Normalize non-standard source_map keys to required standard keys
        report_card.source_map = _normalize_source_map_keys(report_card.source_map)
        # Re-inject data_sources if instructor dropped them
        if not report_card.data_sources and raw_data_sources:
            report_card.data_sources = raw_data_sources
            logger.info(
                "[%s] re-injected data_sources (%d URLs) from raw text", holding.tradingsymbol, len(raw_data_sources)
            )
        elif raw_data_sources:
            # Merge any missing URLs
            existing = set(report_card.data_sources)
            for url in raw_data_sources:
                if url not in existing:
                    report_card.data_sources.append(url)
                    existing.add(url)
        return report_card, completion
    response = await instructor_client.messages.create(
        response_model=AnalystReportCard,
        model=config.analyst_model,
        max_tokens=config.analyst_max_tokens,
        messages=messages,
    )
    raise ValueError(f"Instructor client did not return completion metadata for {holding.tradingsymbol}: {response}")


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


def _extract_urls_from_search_result(text: str) -> list[str]:
    """Extract URLs from Tavily search result text (lines starting with 'URL: ')."""
    urls: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("URL: "):
            url = stripped[5:].strip()
            if url and url.startswith("http"):
                urls.append(url)
    return urls


def _materialize_tool_results(
    response: Any,
    *,
    config: Settings,
    search_budget_remaining: int,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    tool_results: list[dict[str, Any]] = []
    searches_used = 0
    collected_urls: list[str] = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) != "tool_use":
            continue
        tool_name = getattr(block, "name", "")
        if tool_name != "tavily_search":
            payload = json.dumps({"error": f"Unsupported tool requested: {tool_name}"}, ensure_ascii=True)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": payload,
                    "is_error": True,
                }
            )
            continue

        if searches_used >= search_budget_remaining:
            payload = json.dumps(
                {"error": f"tavily_search budget exhausted; max {config.analyst_max_searches} searches allowed."},
                ensure_ascii=True,
            )
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": payload,
                    "is_error": True,
                }
            )
            continue

        tool_input = getattr(block, "input", {}) or {}
        try:
            result = tavily_search(
                query=str(tool_input["query"]),
                max_results=int(tool_input.get("max_results", DEFAULT_TAVILY_MAX_RESULTS)),
                settings=config,
            )
            payload = result
            is_error = False
            searches_used += 1
            collected_urls.extend(_extract_urls_from_search_result(result))
        except Exception as exc:
            payload = json.dumps({"error": str(exc)}, ensure_ascii=True)
            is_error = True

        tool_results.append(
            {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": payload,
                **({"is_error": True} if is_error else {}),
            }
        )
    return tool_results, searches_used, collected_urls


def _log_response_usage(
    *,
    label: str,
    model: str,
    response: Any,
    settings: Settings,
    metadata: dict[str, Any] | None = None,
) -> None:
    record_anthropic_usage(
        settings=settings,
        label=label,
        model=model,
        response=response,
        metadata=metadata,
    )


def _backfill_source_map_from_urls(
    report_card: AnalystReportCard,
    collected_urls: list[str],
) -> AnalystReportCard:
    """Try to fill 'Not available' source_map entries using heuristics on collected URLs.

    Maps financial data site URLs to metrics based on known URL patterns:
    - screener.in, moneycontrol.com ratios pages → roce, roe, pe, debt_to_equity, fcf_yield
    - trendlyne.com, tickertape.in → pe, peg, fair_value
    - livemint.com, bseindia.com results → revenue_cagr, eps_cagr
    - General results/earnings articles → revenue_cagr, eps_cagr
    """
    # Build a pool of available URLs (data_sources + collected)
    url_pool = list(dict.fromkeys(list(report_card.data_sources) + collected_urls))

    # Define heuristic URL→metric mappings
    url_metric_hints: list[tuple[list[str], list[str]]] = [
        (
            ["screener.in", "stockanalysis.com", "moneycontrol.com/financials"],
            ["roce", "roe", "pe", "debt_to_equity", "fcf_yield", "peg"],
        ),
        (["trendlyne.com", "tickertape.in"], ["pe", "peg", "fair_value"]),
        (["livemint.com", "bseindia.com", "nseindia.com"], ["revenue_cagr", "eps_cagr"]),
        (["results", "earnings", "quarterly", "q3", "q2", "q4"], ["revenue_cagr", "eps_cagr"]),
        (["analyst", "target", "consensus", "rating"], ["analyst_target"]),
        (["risk", "outlook", "competitor"], ["risk_1"]),
    ]

    for url in url_pool:
        url_lower = url.lower()
        for patterns, metric_keys in url_metric_hints:
            if any(p in url_lower for p in patterns):
                for mk in metric_keys:
                    if report_card.source_map.get(mk) == "Not available":
                        report_card.source_map[mk] = url
                        break  # Only fill one metric per URL pattern match

    return report_card


def _sync_source_map_to_data_sources(report_card: AnalystReportCard) -> AnalystReportCard:
    """Ensure every URL in source_map also appears in data_sources."""
    existing = set(report_card.data_sources)
    added: list[str] = []
    for url in report_card.source_map.values():
        if url and url.startswith("http") and url not in existing:
            added.append(url)
            existing.add(url)
    if added:
        report_card.data_sources = list(report_card.data_sources) + added
    return report_card


def _overwrite_report_card_with_data_card(
    report_card: AnalystReportCard,
    data_card_sections: dict,
) -> tuple[AnalystReportCard, dict[str, str]]:
    """Overwrite specific string fields in AnalystReportCard with exact data card values.
    Returns updated report_card and a dict of what was overwritten (for the judge)."""
    overwritten: dict[str, str] = {}

    # From quality section
    roe = data_card_sections.get("quality", {}).get("roe_proxy_pct")
    if roe is not None:
        report_card.quality.roe = f"{roe:.1f}% (yfinance API)"
        overwritten["quality.roe"] = report_card.quality.roe

    roce = data_card_sections.get("quality", {}).get("roce_proxy_pct")
    if roce is not None:
        report_card.quality.roce = f"{roce:.1f}% (yfinance API)"
        overwritten["quality.roce"] = report_card.quality.roce

    de = data_card_sections.get("financials", {}).get("debt_to_equity")
    if de is not None:
        report_card.quality.debt_to_equity = f"{de:.2f}x (yfinance API)"
        overwritten["quality.debt_to_equity"] = report_card.quality.debt_to_equity

    # From valuation section
    pe = data_card_sections.get("valuation", {}).get("trailing_pe")
    if pe is not None:
        report_card.valuation.pe = f"{pe:.1f}x TTM (yfinance API)"
        overwritten["valuation.pe"] = report_card.valuation.pe

    sector_pe = data_card_sections.get("valuation", {}).get("sector_pe")
    if sector_pe is not None:
        report_card.valuation.sector_pe = f"{sector_pe:.1f}x (NSE India API)"
        overwritten["valuation.sector_pe"] = report_card.valuation.sector_pe

    peg = data_card_sections.get("valuation", {}).get("peg_ratio")
    if peg is not None:
        report_card.valuation.peg = f"{peg:.2f} (yfinance API)"
        overwritten["valuation.peg"] = report_card.valuation.peg

    # From nse_quarterly — revenue_cagr and eps_cagr
    fiscal = get_fiscal_context()
    nse_q = data_card_sections.get("nse_quarterly", {})
    rev_yoy = nse_q.get("revenue_yoy_pct")
    if rev_yoy is not None:
        report_card.growth_engine.revenue_cagr = f"{rev_yoy:.1f}% YoY ({fiscal['latest_quarter']}) (NSE India API)"
        overwritten["growth_engine.revenue_cagr"] = report_card.growth_engine.revenue_cagr

    # EPS: use latest quarter EPS from quarters list
    quarters = nse_q.get("quarters", [])
    latest_eps = quarters[0].get("eps") if quarters else None
    eps_qoq = nse_q.get("eps_qoq_pct")
    if latest_eps is not None and eps_qoq is not None:
        report_card.growth_engine.eps_cagr = f"₹{latest_eps:.2f} EPS latest quarter, {eps_qoq:.1f}% QoQ (NSE India API)"
        overwritten["growth_engine.eps_cagr"] = report_card.growth_engine.eps_cagr
    elif latest_eps is not None:
        report_card.growth_engine.eps_cagr = f"₹{latest_eps:.2f} EPS latest quarter (NSE India API)"
        overwritten["growth_engine.eps_cagr"] = report_card.growth_engine.eps_cagr

    # From price_data — timing
    vs_200dma = data_card_sections.get("price_data", {}).get("vs_200dma_pct")
    if vs_200dma is not None:
        direction = "above" if vs_200dma > 0 else "below"
        report_card.timing.price_vs_200dma = f"{vs_200dma:.1f}% {direction} 200 DMA (yfinance API)"
        overwritten["timing.price_vs_200dma"] = report_card.timing.price_vs_200dma

    # FII trend from delivery_pct + institutional_holding
    delivery_pct = data_card_sections.get("quality", {}).get("delivery_pct")
    inst_pct = data_card_sections.get("ownership", {}).get("institutional_holding_pct")
    if delivery_pct is not None:
        signal = data_card_sections.get("technical_signals", {}).get("delivery_signal", "Medium")
        inst_str = f", institutional holding {inst_pct:.1f}%" if inst_pct is not None else ""
        report_card.timing.fii_trend = f"Delivery {delivery_pct:.1f}% ({signal} conviction){inst_str} (NSE India API)"
        overwritten["timing.fii_trend"] = report_card.timing.fii_trend

    return report_card, overwritten


def _compute_fair_value(data_card_sections: dict) -> list[float] | None:
    """Compute fair value range from Python-verified data card values.
    Returns [low, high] or None if insufficient data."""
    valuation = data_card_sections.get("valuation", {})
    financials = data_card_sections.get("financials", {})

    forward_eps = financials.get("forward_eps") or financials.get("trailing_eps")
    trailing_pe = valuation.get("trailing_pe")
    sector_pe = valuation.get("sector_pe")
    analyst_target = valuation.get("analyst_target_mean")

    if not forward_eps or forward_eps <= 0:
        return None

    # Base PE = lower of trailing PE and sector PE (conservative anchor)
    base_pe = None
    if trailing_pe and sector_pe:
        base_pe = min(trailing_pe, sector_pe * 1.1)  # cap at 10% sector premium
    elif sector_pe:
        base_pe = sector_pe
    elif trailing_pe:
        base_pe = trailing_pe

    if not base_pe or base_pe <= 0:
        return None

    fair_mid = round(forward_eps * base_pe, 1)
    fair_low = round(fair_mid * 0.85, 1)
    fair_high = round(max(fair_mid * 1.15, analyst_target or 0), 1)

    return [fair_low, fair_high]


def _fix_internal_consistency(report_card: AnalystReportCard) -> AnalystReportCard:
    """Deterministic post-processing to fix common LLM internal inconsistencies."""
    # 1. Recalculate margin_of_safety from fair_value_range and current_price
    fv = report_card.valuation.fair_value_range
    price = report_card.stock_snapshot.current_price
    if len(fv) == 2 and fv[0] > 0 and fv[1] > 0 and price > 0:
        midpoint = (fv[0] + fv[1]) / 2
        mos_pct = (midpoint - price) / price * 100
        if mos_pct >= 0:
            report_card.valuation.margin_of_safety = f"+{mos_pct:.1f}% (discount)"
        else:
            report_card.valuation.margin_of_safety = f"{mos_pct:.1f}% (overvalued)"

    # 2. Fix action_plan zone ordering: stop_loss < buy_zone[0] <= buy_zone[1] < add_zone < trim_zone
    ap = report_card.action_plan
    if len(ap.buy_zone) == 2 and ap.buy_zone[0] > ap.buy_zone[1]:
        ap.buy_zone = [ap.buy_zone[1], ap.buy_zone[0]]
    if len(ap.buy_zone) == 2:
        if ap.stop_loss >= ap.buy_zone[0] and ap.buy_zone[0] > 0:
            ap.stop_loss = round(ap.buy_zone[0] * 0.90, 1)  # 10% below low buy
        if ap.add_zone <= ap.buy_zone[1] and ap.buy_zone[1] > 0:
            ap.add_zone = round(ap.buy_zone[1] * 1.05, 1)  # 5% above high buy
        if ap.trim_zone <= ap.add_zone and ap.add_zone > 0:
            ap.trim_zone = round(ap.add_zone * 1.15, 1)  # 15% above add

    return report_card


DATA_CARD_SOURCE_MAP: dict[str, tuple[str, str, str]] = {
    "pe": ("valuation", "trailing_pe", "yfinance API"),
    "roe": ("quality", "roe_proxy_pct", "yfinance API"),
    "roce": ("quality", "roce_proxy_pct", "yfinance API"),
    "debt_to_equity": ("financials", "debt_to_equity", "yfinance API"),
    "peg": ("valuation", "peg_ratio", "yfinance API"),
    "revenue_cagr": ("nse_quarterly", "revenue_yoy_pct", "NSE India API"),
    "eps_cagr": ("nse_quarterly", "eps_qoq_pct", "NSE India API"),
    "fcf_yield": ("financials", "ebitda_margin_pct", "yfinance API"),
    "analyst_target": ("valuation", "analyst_target_mean", "yfinance API"),
    "market_share": ("meta", "index_memberships", "NSE India API"),
}


def _inject_data_card_sources(
    report_card: AnalystReportCard,
    data_card_sections: dict,
) -> AnalystReportCard:
    """Fill 'Not available' source_map entries with API provider name when data card has a value."""
    for metric_key, (section, field, provider) in DATA_CARD_SOURCE_MAP.items():
        current_value = report_card.source_map.get(metric_key, "Not available")
        if current_value.strip().lower() not in ("not available", ""):
            continue  # already has a real source
        section_data = data_card_sections.get(section)
        if not isinstance(section_data, dict):
            continue
        field_value = section_data.get(field)
        # For list fields (like index_memberships), check non-empty list
        if isinstance(field_value, list):
            has_value = len(field_value) > 0
        else:
            has_value = field_value is not None
        if has_value:
            report_card.source_map[metric_key] = provider
    return report_card


def _build_company_artifact(
    *,
    report_card: AnalystReportCard,
    holding: Holding,
    config: Settings,
    yfinance_data: dict[str, object] | None = None,
) -> CompanyAnalysisArtifact:
    return CompanyAnalysisArtifact(
        generated_at=datetime.now(UTC),
        source_model=config.analyst_model,
        exchange=holding.exchange,
        ticker=holding.tradingsymbol.upper(),
        report_card=report_card,
        yfinance_data=yfinance_data or {},
    )


def _build_company_data_card_artifact(
    *,
    report_card: AnalystReportCard,
    holding: Holding,
    config: Settings,
    data_card_sections: dict,
) -> CompanyDataCard:
    return CompanyDataCard(
        generated_at=datetime.now(UTC),
        source_model=config.analyst_model,
        exchange=holding.exchange,
        ticker=holding.tradingsymbol.upper(),
        analysis=report_card,
        **data_card_sections,
    )


try:
    from langfuse import observe as _lf_observe

    def _analyst_observe(fn: Any) -> Any:
        return _lf_observe(fn, as_type="generation", capture_input=False, capture_output=False)
except ImportError:

    def _analyst_observe(fn: Any) -> Any:  # type: ignore[misc]
        return fn


@_analyst_observe
async def generate_company_artifact(
    holding: Holding,
    price_context: dict[str, float | str],
    skills_content: str,
    client: AsyncAnthropic | Any,
    config: Settings,
    _retries_remaining: int | None = None,
    _retry_context: str | None = None,
) -> CompanyDataCard:
    started = time.perf_counter()
    logger.info("[%s] starting analysis", holding.tradingsymbol)

    # Emit structured input to the active Langfuse generation span
    lf = get_langfuse(config)
    if lf:
        try:
            lf.update_current_generation(
                name=f"analyst-{holding.tradingsymbol}",
                model=config.analyst_model,
                input={
                    "tradingsymbol": holding.tradingsymbol,
                    "exchange": holding.exchange,
                    "last_price": holding.last_price,
                    "pnl_pct": holding.pnl_pct,
                    "current_weight_pct": holding.current_weight_pct,
                    "target_weight_pct": holding.target_weight_pct,
                    "52w_high": price_context.get("52w_high"),
                    "52w_low": price_context.get("52w_low"),
                    "current_vs_52w_high_pct": price_context.get("current_vs_52w_high_pct"),
                },
                metadata={"ticker": holding.tradingsymbol},
            )
        except Exception:
            pass  # non-fatal
    instructor_client = _ensure_instructor_client(client, api_key=config.anthropic_api_key)
    raw_client = _raw_client_for_counting(instructor_client)
    tools = [get_tavily_search_tool_definition(config)]

    # Fetch yfinance raw + NSE India data in parallel
    yf_raw, nse_provider = await asyncio.gather(
        get_yfinance_company_info(holding.tradingsymbol),
        get_nse_india_provider_payload(holding.tradingsymbol),
    )
    yf_raw = yf_raw or {}
    nse_raw = nse_provider.get("raw", {}) if isinstance(nse_provider, dict) else {}

    # Also get the flat snapshot for backward compat (used in verdict yfinance_data field)
    yfinance_data = await get_yfinance_snapshot(holding.tradingsymbol)

    # Build data card sections (Python math, no LLM)
    data_card_sections = build_company_data_card(
        ticker=holding.tradingsymbol,
        exchange=holding.exchange,
        yf_raw=yf_raw,
        nse_raw=nse_raw,
        price_context=price_context,
    )

    fiscal = get_fiscal_context()
    analyst_input = AnalystInputPayload(
        tradingsymbol=holding.tradingsymbol,
        exchange=holding.exchange,
        quantity=holding.quantity,
        average_price=holding.average_price,
        last_price=holding.last_price,
        pnl=holding.pnl,
        pnl_pct=holding.pnl_pct,
        current_weight_pct=holding.current_weight_pct,
        target_weight_pct=holding.target_weight_pct,
        drift=round(holding.current_weight_pct - holding.target_weight_pct, 3),
        **{
            "52w_high": price_context.get("52w_high", 0.0),
            "52w_low": price_context.get("52w_low", 0.0),
            "current_vs_52w_high_pct": price_context.get("current_vs_52w_high_pct", 0.0),
        },
        yfinance_data=yfinance_data,
    )
    user_prompt = (
        f"Analyse this Indian stock. Today: {fiscal['today_date']}. "
        f"Latest published quarter: {fiscal['latest_quarter']}. "
        f"Current period: {fiscal['current_quarter']}.\n\n"
        f"Run exactly {config.analyst_max_searches} tavily_search calls in this order:\n"
        f"  1. '{holding.tradingsymbol} {fiscal['latest_quarter']} quarterly results management commentary guidance'\n"
        f"  2. '{holding.tradingsymbol} management quality competitive moat market share {fiscal['current_fy']}'\n"
        f"  3. '{holding.tradingsymbol} risks regulatory sector outlook {fiscal['current_fy']}'\n"
        f"  4. '{holding.tradingsymbol} analyst target price consensus rating {fiscal['current_fy']}'\n\n"
        "Capture the exact URL from every search result you use — add them all to data_sources.\n"
        "Fill all 12 source_map keys: revenue_cagr, eps_cagr, roce, roe, pe, peg, fcf_yield, debt_to_equity, fair_value, risk_1, analyst_target, market_share.\n"
        "source_map values MUST be URLs (https://...) or 'Not available'. NEVER put data values in source_map.\n"
        "NEVER cite a 5-year or 3-year historical CAGR. Use only the latest 1-2 quarters YoY trend.\n"
        "eps_cagr MUST be per-share EPS, NOT absolute net profit in crores.\n"
        "Return exactly one valid JSON object. No markdown fences. No text outside the JSON.\n\n"
        "## Pre-Computed Data Card (use these FACTS, do not recompute):\n"
        + json.dumps(data_card_sections, indent=2, default=str)
        + "\n\n## Portfolio Input:\n"
        + analyst_input.model_dump_json(by_alias=True)
    )

    # Build explicit injection block from data_card_sections
    injected_values: list[str] = []
    _pe = data_card_sections.get("valuation", {}).get("trailing_pe")
    _sector_pe = data_card_sections.get("valuation", {}).get("sector_pe")
    _pe_premium = data_card_sections.get("valuation", {}).get("pe_premium_to_sector_pct")
    _roe = data_card_sections.get("quality", {}).get("roe_proxy_pct")
    _roce = data_card_sections.get("quality", {}).get("roce_proxy_pct")
    _de = data_card_sections.get("financials", {}).get("debt_to_equity")
    _rev_yoy = data_card_sections.get("nse_quarterly", {}).get("revenue_yoy_pct")
    _rev_qoq = data_card_sections.get("nse_quarterly", {}).get("revenue_qoq_pct")
    _vs_200dma = data_card_sections.get("price_data", {}).get("vs_200dma_pct")
    _alpha = data_card_sections.get("price_data", {}).get("alpha_vs_nifty_52w_pct")
    _delivery = data_card_sections.get("quality", {}).get("delivery_pct")
    if _pe:
        injected_values.append(f"- Trailing PE: {_pe:.1f}x (TTM, yfinance)")
    if _sector_pe:
        injected_values.append(f"- Sector PE: {_sector_pe:.1f}x (NSE India)")
    if _pe_premium:
        injected_values.append(f"- PE vs sector: {_pe_premium:+.1f}%")
    if _roe:
        injected_values.append(f"- ROE: {_roe:.1f}% (yfinance)")
    if _roce:
        injected_values.append(f"- ROCE: {_roce:.1f}% (yfinance)")
    if _de is not None:
        injected_values.append(f"- Debt/Equity: {_de:.2f}x (yfinance)")
    if _rev_yoy:
        injected_values.append(f"- Revenue YoY ({fiscal['latest_quarter']}): {_rev_yoy:.1f}% (NSE India)")
    if _rev_qoq:
        injected_values.append(f"- Revenue QoQ: {_rev_qoq:.1f}% (NSE India)")
    if _vs_200dma:
        injected_values.append(f"- Price vs 200 DMA: {_vs_200dma:.1f}% (yfinance)")
    if _alpha:
        injected_values.append(f"- Alpha vs Nifty 52w: {_alpha:.1f}% (yfinance)")
    if _delivery:
        injected_values.append(f"- Delivery %: {_delivery:.1f}% (NSE India)")
    if injected_values:
        injected_block = (
            "\n\n## PRE-COMPUTED FACTS — USE THESE EXACT VALUES IN YOUR REPORT CARD\n"
            "Do NOT compute your own versions. Copy these directly:\n"
            + "\n".join(injected_values)
            + "\n\nUse these exact values in your report card to ensure internal consistency.\n"
        )
    else:
        injected_block = ""
    user_prompt = user_prompt + injected_block

    if _retry_context:
        user_prompt = _retry_context + "\n\n" + user_prompt
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]
    system_prompt = skills_content
    logger.info(
        "[%s] analyst prompt token estimate: ~%s",
        holding.tradingsymbol,
        estimate_input_tokens(messages=messages, system=system_prompt),
    )
    searches_used = 0
    all_collected_urls: list[str] = []

    for iteration in range(1, MAX_ANALYST_ITERATIONS + 1):
        await _log_input_tokens(
            label=f"[{holding.tradingsymbol}]",
            client=instructor_client,
            model=config.analyst_model,
            messages=messages,
            system=system_prompt,
            tools=tools,
        )
        call_kwargs = {
            "model": config.analyst_model,
            "max_tokens": config.analyst_max_tokens,
            "system": system_prompt,
            "messages": messages,
            "tools": tools,
        }
        # Force at least one tool call on the first iteration so Tavily searches always run
        if searches_used == 0:
            call_kwargs["tool_choice"] = {"type": "any"}
        response = await raw_client.messages.create(**call_kwargs)
        _log_response_usage(
            label=f"[{holding.tradingsymbol}] analyst",
            model=config.analyst_model,
            response=response,
            settings=config,
            metadata={
                "phase": "analyst",
                "ticker": holding.tradingsymbol,
                "iteration": iteration,
            },
        )
        stop_reason = getattr(response, "stop_reason", None)

        if stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": _serialize_content_blocks(response.content)})
            continue

        if stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": _serialize_content_blocks(response.content)})
            tool_results, search_increment, new_urls = _materialize_tool_results(
                response,
                config=config,
                search_budget_remaining=max(config.analyst_max_searches - searches_used, 0),
            )
            searches_used += search_increment
            all_collected_urls.extend(new_urls)
            if tool_results:
                messages.append({"role": "user", "content": tool_results})
            continue

        if stop_reason in {"end_turn", "max_tokens"}:
            report_card, structured_response = await _coerce_report_card_with_instructor(
                instructor_client=instructor_client,
                config=config,
                holding=holding,
                raw_text=_extract_text(response),
            )
            report_card = _fix_internal_consistency(report_card)
            report_card, overwritten_fields = _overwrite_report_card_with_data_card(report_card, data_card_sections)
            # Apply computed fair value range
            computed_fv = _compute_fair_value(data_card_sections)
            if computed_fv:
                report_card.valuation.fair_value_range = computed_fv
                overwritten_fields["valuation.fair_value_range"] = str(computed_fv)
                # Update source_map
                if report_card.source_map.get("fair_value") in ("Not available", "", None):
                    report_card.source_map["fair_value"] = "yfinance API + NSE India API"
                # Recalculate margin of safety
                price = report_card.stock_snapshot.current_price
                if price > 0:
                    midpoint = (computed_fv[0] + computed_fv[1]) / 2
                    mos_pct = (midpoint - price) / price * 100
                    report_card.valuation.margin_of_safety = (
                        f"+{mos_pct:.1f}% (discount)" if mos_pct >= 0 else f"{mos_pct:.1f}% (premium)"
                    )
                    overwritten_fields["valuation.margin_of_safety"] = report_card.valuation.margin_of_safety
            report_card = _inject_data_card_sources(report_card, data_card_sections)
            # Inject real Tavily URLs collected during tool calls (filter to relevant ones only)
            if all_collected_urls:
                ticker_lower = holding.tradingsymbol.lower()
                relevant_urls = [
                    u
                    for u in all_collected_urls
                    if ticker_lower in u.lower()
                    or any(
                        kw in u.lower()
                        for kw in [
                            "screener.in",
                            "moneycontrol.com",
                            "trendlyne.com",
                            "tickertape.in",
                            "bseindia.com",
                            "nseindia.com",
                            "stockanalysis.com",
                        ]
                    )
                ]
                # Fall back to all URLs if filtering leaves nothing
                urls_to_add = relevant_urls if relevant_urls else list(dict.fromkeys(all_collected_urls))[:5]
                unique_urls = list(dict.fromkeys(urls_to_add))
                existing = set(report_card.data_sources)
                for url in unique_urls:
                    if url not in existing:
                        report_card.data_sources.append(url)
                        existing.add(url)
                report_card = _backfill_source_map_from_urls(report_card, unique_urls)
            report_card = _sync_source_map_to_data_sources(report_card)
            _log_response_usage(
                label=f"[{holding.tradingsymbol}] analyst_structured",
                model=config.analyst_model,
                response=structured_response,
                settings=config,
                metadata={
                    "phase": "analyst_structured",
                    "ticker": holding.tradingsymbol,
                    "iteration": iteration,
                },
            )

            artifact = _build_company_data_card_artifact(
                report_card=report_card,
                holding=holding,
                config=config,
                data_card_sections=data_card_sections,
            )
            output_path = save_company_analysis_artifact(artifact, settings=config)
            logger.info(
                "[%s] saved company analysis to %s in %.1fs",
                holding.tradingsymbol,
                output_path,
                time.perf_counter() - started,
            )

            report_card_json = artifact.analysis.model_dump_json()

            # Build compact data card summary for judge context
            data_card_summary = {
                "price": {
                    "cmp": data_card_sections.get("price_data", {}).get("cmp"),
                    "vs_200dma_pct": data_card_sections.get("price_data", {}).get("vs_200dma_pct"),
                    "alpha_vs_nifty": data_card_sections.get("price_data", {}).get("alpha_vs_nifty_52w_pct"),
                },
                "valuation": {
                    "trailing_pe": data_card_sections.get("valuation", {}).get("trailing_pe"),
                    "sector_pe": data_card_sections.get("valuation", {}).get("sector_pe"),
                    "pe_premium_pct": data_card_sections.get("valuation", {}).get("pe_premium_to_sector_pct"),
                    "peg": data_card_sections.get("valuation", {}).get("peg_ratio"),
                    "analyst_target_mean": data_card_sections.get("valuation", {}).get("analyst_target_mean"),
                },
                "financials": {
                    "debt_to_equity": data_card_sections.get("financials", {}).get("debt_to_equity"),
                    "ebitda_margin_pct": data_card_sections.get("financials", {}).get("ebitda_margin_pct"),
                    "net_cash": data_card_sections.get("financials", {}).get("net_cash"),
                    "revenue_growth_pct": data_card_sections.get("financials", {}).get("revenue_growth_pct"),
                },
                "quality": {
                    "roe_proxy_pct": data_card_sections.get("quality", {}).get("roe_proxy_pct"),
                    "roce_proxy_pct": data_card_sections.get("quality", {}).get("roce_proxy_pct"),
                    "delivery_pct": data_card_sections.get("quality", {}).get("delivery_pct"),
                },
                "nse_quarterly": {
                    "revenue_qoq_pct": data_card_sections.get("nse_quarterly", {}).get("revenue_qoq_pct"),
                    "revenue_yoy_pct": data_card_sections.get("nse_quarterly", {}).get("revenue_yoy_pct"),
                    "eps_qoq_pct": data_card_sections.get("nse_quarterly", {}).get("eps_qoq_pct"),
                    "latest_quarter": (
                        data_card_sections.get("nse_quarterly", {}).get("quarters", [{}])[0]
                        if data_card_sections.get("nse_quarterly", {}).get("quarters")
                        else {}
                    ),
                },
                "ownership": {
                    "promoter_holding_pct": data_card_sections.get("ownership", {}).get("promoter_holding_pct"),
                    "promoter_qoq_change": data_card_sections.get("ownership", {}).get("promoter_holding_qoq_change"),
                },
            }
            data_card_context = json.dumps(data_card_summary, ensure_ascii=True)
            overwritten_fields_context = json.dumps(overwritten_fields, indent=2, ensure_ascii=True)

            judge_result = await judge_report_card(
                report_card_json=report_card_json,
                ticker=holding.tradingsymbol,
                config=config,
                client=raw_client,
                data_card_context=data_card_context,
                overwritten_fields_context=overwritten_fields_context,
            )
            factual_result = await judge_factual_grounding(
                report_card_json=report_card_json,
                ticker=holding.tradingsymbol,
                config=config,
                client=raw_client,
                data_card_context=data_card_context,
                overwritten_fields_context=overwritten_fields_context,
            )
            if judge_result:
                logger.info(
                    "[%s] quality judge overall=%d — %s",
                    holding.tradingsymbol,
                    judge_result.get("overall", 0),
                    judge_result.get("one_line_summary", ""),
                )
            if factual_result:
                logger.info(
                    "[%s] factual judge overall=%d — %s",
                    holding.tradingsymbol,
                    factual_result.get("overall", 0),
                    factual_result.get("one_line_summary", ""),
                )

            # Compute combined score (50/50 quality + factual)
            quality_overall = judge_result.get("overall", 0) if judge_result else 0
            factual_overall = factual_result.get("overall", 0) if factual_result else 0
            if judge_result and factual_result:
                combined_overall = quality_overall * 0.5 + factual_overall * 0.5
            elif judge_result:
                combined_overall = float(quality_overall)
            elif factual_result:
                combined_overall = float(factual_overall)
            else:
                combined_overall = 0.0

            passed = combined_overall >= config.judge_retry_threshold

            # Persist judge scores locally
            save_judge_scores(
                ticker=holding.tradingsymbol,
                quality_scores=judge_result,
                factual_scores=factual_result,
                combined_overall=combined_overall,
                passed=passed,
                settings=config,
            )

            # Post judge scores on the active trace (inside @observe context)
            if lf:
                if judge_result:
                    score_active_trace(lf, judge_result, holding.tradingsymbol, factual_result)

            # Emit structured output to the active Langfuse generation span
            if lf:
                try:
                    rc = artifact.analysis
                    lf.update_current_generation(
                        output={
                            "verdict": rc.final_verdict.verdict,
                            "confidence": rc.final_verdict.confidence,
                            "growth_score": rc.growth_engine.growth_score,
                            "risk_level": rc.risk_matrix.risk_level,
                            "timing_signal": rc.timing.timing_signal,
                            "data_sources": rc.data_sources,
                            "judge_overall": judge_result.get("overall") if judge_result else None,
                            "factual_overall": factual_result.get("overall") if factual_result else None,
                            "combined_overall": combined_overall,
                        },
                        metadata={
                            "ticker": holding.tradingsymbol,
                            "verdict": rc.final_verdict.verdict,
                            "iterations": iteration,
                        },
                    )
                except Exception:
                    pass  # non-fatal

            # Retry if combined score is below threshold
            retries = _retries_remaining if _retries_remaining is not None else config.judge_max_retries
            if not passed and retries > 0:
                issues = []
                if judge_result:
                    issues.extend(judge_result.get("key_issues", []))
                if factual_result:
                    issues.extend(factual_result.get("red_flags", []))
                logger.warning(
                    "[%s] combined judge score %.1f < %d, retrying (%d left). Issues: %s",
                    holding.tradingsymbol,
                    combined_overall,
                    config.judge_retry_threshold,
                    retries,
                    "; ".join(issues[:5]) if issues else "none captured",
                )
                # Build retry context with judge feedback and available URLs
                retry_parts = [
                    f"RETRY — Your previous analysis scored {combined_overall:.0f}/100. Fix these issues:",
                ]
                for issue in issues[:5]:
                    retry_parts.append(f"- {issue}")
                if all_collected_urls:
                    unique_urls = list(dict.fromkeys(all_collected_urls))  # dedupe, preserve order
                    retry_parts.append("")
                    retry_parts.append(
                        "Source URLs available from your searches (USE THESE in source_map and data_sources):"
                    )
                    for url in unique_urls:
                        retry_parts.append(f"- {url}")
                retry_ctx = "\n".join(retry_parts)

                # Delete the saved artifact and re-run from scratch
                output_path.unlink(missing_ok=True)
                return await generate_company_artifact(
                    holding=holding,
                    price_context=price_context,
                    skills_content=skills_content,
                    client=client,
                    config=config,
                    _retries_remaining=retries - 1,
                    _retry_context=retry_ctx,
                )

            if not passed:
                logger.warning(
                    "[%s] combined judge score %.1f < %d after all retries; proceeding anyway",
                    holding.tradingsymbol,
                    combined_overall,
                    config.judge_retry_threshold,
                )

            return artifact

        raise ValueError(f"Unexpected stop_reason: {stop_reason}")

    raise ValueError("Analyst sub-agent exceeded MAX_ANALYST_ITERATIONS.")


async def analyse_stock(
    holding: Holding,
    portfolio_total_value: float,
    price_context: dict[str, float | str],
    skills_content: str,
    client: AsyncAnthropic | Any,
    config: Settings,
) -> StockVerdict:
    """
    Single-stock sub-agent. One focused Claude API call with Tavily-backed search.
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


async def generate_yfinance_only_company_artifact(
    holding: Holding,
    client: AsyncAnthropic | Any,
    config: Settings,
) -> CompanyDataCard:
    raw_company_info = await get_yfinance_company_info(holding.tradingsymbol)
    yfinance_data = await get_yfinance_snapshot(holding.tradingsymbol)
    if not raw_company_info or not yfinance_data:
        raise ValueError(f"Yahoo Finance data unavailable for {holding.tradingsymbol}")

    instructor_client = _ensure_instructor_client(client, api_key=config.anthropic_api_key)
    system_prompt = (
        "You convert Yahoo Finance company data into a valid AnalystReportCard JSON object. "
        "Use only the provided JSON. Do not browse, do not search, and do not invent unsupported facts. "
        "When a field is missing, use 'Not available' for strings, 0.0 for numeric arrays, and [] for lists. "
        "Set data_sources to [] and set all 12 source_map keys to 'Not available'."
    )
    messages = [
        {
            "role": "user",
            "content": (
                "Convert this Yahoo Finance company data into AnalystReportCard JSON. "
                "Use direct numeric fields exactly when present. "
                "Keep the verdict conservative. If evidence is incomplete, prefer HOLD and LOW confidence.\n\n"
                f"Ticker: {holding.tradingsymbol}\n"
                f"Flat snapshot:\n{json.dumps(yfinance_data, ensure_ascii=True)}\n\n"
                f"Raw Yahoo Finance JSON:\n{json.dumps(raw_company_info, ensure_ascii=True)}"
            ),
        }
    ]
    await _log_input_tokens(
        label=f"[{holding.tradingsymbol}] [yfinance-only]",
        client=instructor_client,
        model=config.analyst_model,
        messages=messages,
        system=system_prompt,
    )
    if hasattr(instructor_client.messages, "create_with_completion"):
        report_card, completion = await instructor_client.messages.create_with_completion(
            response_model=AnalystReportCard,
            model=config.analyst_model,
            max_tokens=config.analyst_max_tokens,
            system=system_prompt,
            messages=messages,
        )
        _log_response_usage(
            label=f"[{holding.tradingsymbol}] analyst_yfinance_only",
            model=config.analyst_model,
            response=completion,
            settings=config,
            metadata={"phase": "analyst_yfinance_only", "ticker": holding.tradingsymbol},
        )
    else:
        report_card = await instructor_client.messages.create(
            response_model=AnalystReportCard,
            model=config.analyst_model,
            max_tokens=config.analyst_max_tokens,
            system=system_prompt,
            messages=messages,
        )
    report_card = _fix_internal_consistency(report_card)
    report_card.data_sources = []
    report_card.source_map = {key: "Not available" for key in REQUIRED_SOURCE_MAP_KEYS}
    report_card.final_verdict.confidence = "Low"
    # Build minimal data card sections from yfinance only (nse_raw={})
    data_card_sections = build_company_data_card(
        ticker=holding.tradingsymbol,
        exchange=holding.exchange,
        yf_raw=raw_company_info,
        nse_raw={},
        price_context={},
    )
    artifact = _build_company_data_card_artifact(
        report_card=report_card,
        holding=holding,
        config=config,
        data_card_sections=data_card_sections,
    )
    save_company_analysis_artifact(artifact, settings=config)
    return artifact


def _provider_compare_dir(config: Settings) -> Path:
    path = config.kite_data_dir / "provider_compare"
    path.mkdir(parents=True, exist_ok=True)
    return path


async def export_provider_comparison_files(
    ticker: str,
    *,
    exchange: str,
    config: Settings,
) -> list[Path]:
    normalized_ticker = str(ticker).strip().upper()
    fetched_at = datetime.now(UTC).isoformat()
    yahoo_task = get_yfinance_provider_payload(normalized_ticker)
    nse_task = get_nse_india_provider_payload(normalized_ticker)
    yahoo_payload, nse_payload = await asyncio.gather(yahoo_task, nse_task)

    provider_payloads = (
        ("yfinance", yahoo_payload),
        ("nse_india", nse_payload),
    )
    output_dir = _provider_compare_dir(config)
    saved_paths: list[Path] = []
    for provider_name, payload in provider_payloads:
        enriched_payload = {
            "provider": provider_name,
            "ticker": normalized_ticker,
            "exchange": exchange.upper(),
            "fetched_at": fetched_at,
            **payload,
        }
        path = output_dir / f"{normalized_ticker}_{provider_name}.json"
        path.write_text(json.dumps(enriched_payload, indent=2, ensure_ascii=True), encoding="utf-8")
        saved_paths.append(path)
    return saved_paths
