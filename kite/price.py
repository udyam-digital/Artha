from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from kite.coerce import _coerce_float
from providers.mcp_client import ToolExecutionError

logger = logging.getLogger(__name__)


def _extract_holdings_payload(raw_response: Any) -> list[dict[str, Any]]:
    if isinstance(raw_response, list):
        return [item for item in raw_response if isinstance(item, dict)]
    if isinstance(raw_response, dict):
        for key in ("holdings", "data", "items"):
            value = raw_response.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


async def kite_get_price_history(
    kite_client: Any,
    tradingsymbol: str,
    instrument_token: int,
    days: int = 365,
) -> dict[str, Any]:
    end_date = datetime.now(UTC)
    start_date = end_date - timedelta(days=days)

    try:
        raw_history = await kite_client.call_tool(
            "get_historical_data",
            {
                "instrument_token": instrument_token,
                "interval": "day",
                "from_date": start_date.date().isoformat(),
                "to_date": end_date.date().isoformat(),
            },
        )
    except Exception as exc:
        raise ToolExecutionError(f"Price history fetch failed for {tradingsymbol}: {exc}") from exc

    candles = _extract_holdings_payload(raw_history)
    if not candles and isinstance(raw_history, dict):
        data = raw_history.get("candles") or raw_history.get("data")
        if isinstance(data, list):
            candles = data

    parsed: list[dict[str, float]] = []
    for candle in candles:
        if isinstance(candle, dict):
            parsed.append(
                {
                    "close": _coerce_float(candle.get("close")),
                    "high": _coerce_float(candle.get("high")),
                    "low": _coerce_float(candle.get("low")),
                }
            )
        elif isinstance(candle, list) and len(candle) >= 5:
            parsed.append(
                {
                    "high": _coerce_float(candle[2]),
                    "low": _coerce_float(candle[3]),
                    "close": _coerce_float(candle[4]),
                }
            )

    if not parsed:
        raise ToolExecutionError(f"No historical data available for {tradingsymbol}")

    closes = [row["close"] for row in parsed if row["close"] > 0]
    highs = [row["high"] for row in parsed if row["high"] > 0]
    lows = [row["low"] for row in parsed if row["low"] > 0]
    current_price = closes[-1] if closes else 0.0
    price_1y_ago = closes[0] if closes else 0.0
    high_52w = max(highs) if highs else 0.0
    low_52w = min(lows) if lows else 0.0

    return {
        "52w_high": high_52w,
        "52w_low": low_52w,
        "current_vs_52w_high_pct": ((current_price / high_52w) - 1) * 100.0 if high_52w else 0.0,
        "price_1y_ago": price_1y_ago,
        "price_change_1y_pct": ((current_price / price_1y_ago) - 1) * 100.0 if price_1y_ago else 0.0,
    }
