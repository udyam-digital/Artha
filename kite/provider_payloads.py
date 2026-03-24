from __future__ import annotations

import logging
from typing import Any

from config import get_settings
from kite.coerce import _coerce_json_object, _coerce_optional_float
from providers.mcp_client import MCPToolClient
from providers.nse import load_nse_server_definition

logger = logging.getLogger(__name__)


def _unwrap_tool_payload(payload: Any) -> Any:
    current = payload
    for _ in range(3):
        if isinstance(current, dict) and "result" in current and len(current) == 1:
            current = current["result"]
            continue
        break
    return _coerce_json_object(current)


async def get_nse_india_provider_payload(ticker: str) -> dict[str, Any]:
    settings = get_settings()
    normalized_ticker = str(ticker).strip().upper()
    result: dict[str, Any] = {
        "provider": "nse_india",
        "requested_ticker": normalized_ticker,
        "provider_symbol": normalized_ticker,
        "snapshot": {},
        "raw": {},
        "errors": [],
    }
    if not normalized_ticker:
        result["errors"].append("blank ticker")
        return result
    try:
        definition = load_nse_server_definition(settings)
        async with MCPToolClient(definition, timeout_seconds=settings.nse_mcp_timeout_seconds) as client:
            for label, tool_name in (
                ("details", "get_equity_details"),
                ("trade_info", "get_equity_trade_info"),
                ("corporate_info", "get_equity_corporate_info"),
            ):
                try:
                    payload = await client.call_tool(tool_name, {"symbol": normalized_ticker})
                    result["raw"][label] = _unwrap_tool_payload(payload)
                except Exception as exc:
                    result["raw"][label] = {}
                    result["errors"].append(f"{label}: {exc}")
    except Exception as exc:
        logger.warning("NSE India provider fetch failed for %s: %s", normalized_ticker, exc)
        result["errors"].append(str(exc))
        return result
    details = result["raw"].get("details") if isinstance(result["raw"], dict) else {}
    trade_info = result["raw"].get("trade_info") if isinstance(result["raw"], dict) else {}
    info = details.get("info") if isinstance(details, dict) and isinstance(details.get("info"), dict) else {}
    price_info = (
        details.get("priceInfo") if isinstance(details, dict) and isinstance(details.get("priceInfo"), dict) else {}
    )
    metadata = (
        details.get("metadata") if isinstance(details, dict) and isinstance(details.get("metadata"), dict) else {}
    )
    security_info = (
        details.get("securityInfo")
        if isinstance(details, dict) and isinstance(details.get("securityInfo"), dict)
        else {}
    )
    market_book = (
        trade_info.get("marketDeptOrderBook")
        if isinstance(trade_info, dict) and isinstance(trade_info.get("marketDeptOrderBook"), dict)
        else {}
    )
    result["snapshot"] = {
        "company_name": info.get("companyName") or info.get("symbol") or normalized_ticker,
        "industry": info.get("industry"),
        "sector": security_info.get("boardStatus"),
        "last_price": _coerce_optional_float(price_info.get("lastPrice") or price_info.get("lastPriceDisplay")),
        "previous_close": _coerce_optional_float(price_info.get("previousClose")),
        "day_high": _coerce_optional_float(
            price_info.get("intraDayHighLow", {}).get("max")
            if isinstance(price_info.get("intraDayHighLow"), dict)
            else None
        ),
        "day_low": _coerce_optional_float(
            price_info.get("intraDayHighLow", {}).get("min")
            if isinstance(price_info.get("intraDayHighLow"), dict)
            else None
        ),
        "fifty_two_week_high": _coerce_optional_float(
            price_info.get("weekHighLow", {}).get("max") if isinstance(price_info.get("weekHighLow"), dict) else None
        ),
        "fifty_two_week_low": _coerce_optional_float(
            price_info.get("weekHighLow", {}).get("min") if isinstance(price_info.get("weekHighLow"), dict) else None
        ),
        "market_cap": _coerce_optional_float(metadata.get("marketCap")),
        "listing_date": metadata.get("listingDate"),
        "is_fno": info.get("isFNOSec"),
        "active_series": metadata.get("activeSeries"),
        "delivery_to_traded_qty": _coerce_optional_float(security_info.get("delivToTradedQty")),
        "total_traded_volume": _coerce_optional_float(market_book.get("totalTradedVolume")),
        "total_traded_value": _coerce_optional_float(market_book.get("totalTradedValue")),
    }
    return result
