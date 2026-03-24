from __future__ import annotations

import logging
from typing import Any

from config import get_settings
from kite.coerce import _coerce_json_object, _coerce_optional_float, _coerce_optional_int, _coerce_percent
from providers.mcp_client import MCPToolClient
from providers.yfinance import load_yfinance_server_definition

logger = logging.getLogger(__name__)

_YFINANCE_FIELDS = (
    "ticker",
    "cmp",
    "fifty_two_week_low",
    "fifty_two_week_high",
    "trailing_pe",
    "forward_pe",
    "price_to_book",
    "revenue_growth_pct",
    "earnings_growth_pct",
    "profit_margin_pct",
    "analyst_count",
    "target_mean_price",
    "target_median_price",
    "upside_pct",
    "sector",
    "industry",
)


def _empty_yfinance_snapshot(ticker: str) -> dict[str, Any]:
    return {key: (ticker if key == "ticker" else None) for key in _YFINANCE_FIELDS}


def _normalize_yfinance_ticker(ticker_ns: str) -> str:
    normalized = str(ticker_ns).strip().upper()
    if not normalized:
        return normalized
    return normalized if normalized.endswith(".NS") else f"{normalized}.NS"


def _is_missing_company_response(payload: Any) -> bool:
    if isinstance(payload, dict):
        raw_text = str(payload.get("raw_text", "")).lower()
        return "not found" in raw_text or "error:" in raw_text
    return isinstance(payload, str) and ("not found" in payload.lower() or "error:" in payload.lower())


def map_yfinance_snapshot(ticker: str, raw_payload: dict[str, Any]) -> dict[str, Any]:
    snapshot = _empty_yfinance_snapshot(ticker)
    cmp_value = _coerce_optional_float(raw_payload.get("currentPrice"))
    if cmp_value is None:
        cmp_value = _coerce_optional_float(raw_payload.get("regularMarketPrice"))
    target_mean_price = _coerce_optional_float(raw_payload.get("targetMeanPrice"))
    snapshot.update(
        {
            "cmp": cmp_value,
            "fifty_two_week_low": _coerce_optional_float(raw_payload.get("fiftyTwoWeekLow")),
            "fifty_two_week_high": _coerce_optional_float(raw_payload.get("fiftyTwoWeekHigh")),
            "trailing_pe": _coerce_optional_float(raw_payload.get("trailingPE")),
            "forward_pe": _coerce_optional_float(raw_payload.get("forwardPE")),
            "price_to_book": _coerce_optional_float(raw_payload.get("priceToBook")),
            "revenue_growth_pct": _coerce_percent(raw_payload.get("revenueGrowth")),
            "earnings_growth_pct": _coerce_percent(
                raw_payload.get("earningsGrowth") or raw_payload.get("earningsQuarterlyGrowth")
            ),
            "profit_margin_pct": _coerce_percent(raw_payload.get("profitMargins")),
            "analyst_count": _coerce_optional_int(raw_payload.get("numberOfAnalystOpinions")),
            "target_mean_price": target_mean_price,
            "target_median_price": _coerce_optional_float(raw_payload.get("targetMedianPrice")),
            "sector": str(raw_payload.get("sector")) if raw_payload.get("sector") else None,
            "industry": str(raw_payload.get("industry")) if raw_payload.get("industry") else None,
        }
    )
    snapshot["upside_pct"] = (
        round(((target_mean_price - cmp_value) / cmp_value) * 100.0, 2)
        if cmp_value and target_mean_price is not None
        else None
    )
    return snapshot


async def get_yfinance_snapshot(ticker_ns: str) -> dict[str, Any]:
    settings = get_settings()
    ticker = _normalize_yfinance_ticker(ticker_ns)
    if not ticker:
        return {}

    try:
        definition = load_yfinance_server_definition(settings)
        async with MCPToolClient(definition, timeout_seconds=settings.yfinance_mcp_timeout_seconds) as client:
            raw_payload = await client.call_tool("get_stock_info", {"ticker": ticker})
    except Exception as exc:
        logger.warning("Yahoo Finance snapshot fetch failed for %s: %s", ticker, exc)
        return {}

    if isinstance(raw_payload, dict) and "result" in raw_payload:
        raw_payload = _coerce_json_object(raw_payload["result"])
    else:
        raw_payload = _coerce_json_object(raw_payload)

    if _is_missing_company_response(raw_payload):
        logger.warning("Yahoo Finance returned no company payload for %s", ticker)
        return {}

    if not isinstance(raw_payload, dict):
        logger.warning("Yahoo Finance returned non-dict payload for %s", ticker)
        return {}

    return map_yfinance_snapshot(ticker, raw_payload)


async def get_yfinance_company_info(ticker_ns: str) -> dict[str, Any]:
    settings = get_settings()
    ticker = _normalize_yfinance_ticker(ticker_ns)
    if not ticker:
        return {}

    try:
        definition = load_yfinance_server_definition(settings)
        async with MCPToolClient(definition, timeout_seconds=settings.yfinance_mcp_timeout_seconds) as client:
            raw_payload = await client.call_tool("get_stock_info", {"ticker": ticker})
    except Exception as exc:
        logger.warning("Yahoo Finance company info fetch failed for %s: %s", ticker, exc)
        return {}

    if isinstance(raw_payload, dict) and "result" in raw_payload:
        raw_payload = _coerce_json_object(raw_payload["result"])
    else:
        raw_payload = _coerce_json_object(raw_payload)

    if _is_missing_company_response(raw_payload) or not isinstance(raw_payload, dict):
        return {}
    return raw_payload


async def get_yfinance_provider_payload(ticker_ns: str) -> dict[str, Any]:
    ticker = _normalize_yfinance_ticker(ticker_ns)
    raw_company_info = await get_yfinance_company_info(ticker_ns)
    snapshot = await get_yfinance_snapshot(ticker_ns)
    errors: list[str] = []
    if not raw_company_info:
        errors.append("raw company info unavailable")
    if not snapshot:
        errors.append("flat snapshot unavailable")
    return {
        "provider": "yfinance",
        "requested_ticker": str(ticker_ns).strip().upper(),
        "provider_symbol": ticker,
        "snapshot": snapshot,
        "raw": raw_company_info,
        "errors": errors,
    }
