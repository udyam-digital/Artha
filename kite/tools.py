from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from config import Settings, get_settings
from kite.client import MCPToolClient, KiteMCPClient, ToolExecutionError, load_yfinance_server_definition
from models import Holding, MFHolding, MFSnapshot, MacroContext, PortfolioSnapshot
from rebalance import PASSIVE_INSTRUMENTS
from search.tavily import DEFAULT_TAVILY_MAX_RESULTS, get_tavily_search_tool_definition, tavily_search


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
_MACRO_CONTEXT_CACHE: dict[str, MacroContext] = {}
_MOSPI_CPI_URL = "https://api.mospi.gov.in/api/getCPIIndex"
_MOSPI_IIP_URL_CANDIDATES = (
    "https://api.mospi.gov.in/api/getIIPIndex",
    "https://api.mospi.gov.in/api/getIIPGeneralIndex",
)
_MOSPI_GDP_URL_CANDIDATES = (
    "https://api.mospi.gov.in/api/getNASData",
    "https://api.mospi.gov.in/api/getNASIndex",
)


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_optional_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_optional_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_percent(value: Any) -> float | None:
    numeric = _coerce_optional_float(value)
    if numeric is None:
        return None
    return round(numeric * 100.0, 2)


def _empty_yfinance_snapshot(ticker: str) -> dict[str, Any]:
    return {key: (ticker if key == "ticker" else None) for key in _YFINANCE_FIELDS}


def _normalize_yfinance_ticker(ticker_ns: str) -> str:
    normalized = str(ticker_ns).strip().upper()
    if not normalized:
        return normalized
    return normalized if normalized.endswith(".NS") else f"{normalized}.NS"


def _map_yfinance_snapshot(ticker: str, raw_payload: dict[str, Any]) -> dict[str, Any]:
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
            "earnings_growth_pct": _coerce_percent(raw_payload.get("earningsGrowth") or raw_payload.get("earningsQuarterlyGrowth")),
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


def _is_missing_company_response(payload: Any) -> bool:
    if isinstance(payload, dict):
        raw_text = str(payload.get("raw_text", "")).lower()
        return "not found" in raw_text or "error:" in raw_text
    return isinstance(payload, str) and ("not found" in payload.lower() or "error:" in payload.lower())


def _coerce_json_object(payload: Any) -> Any:
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return payload
    return payload


def _extract_mospi_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "records", "result", "response", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                nested = _extract_mospi_records(value)
                if nested:
                    return nested
    return []


def _find_value(record: dict[str, Any], *keys: str) -> Any:
    lowered = {str(key).lower(): value for key, value in record.items()}
    for key in keys:
        if key.lower() in lowered:
            return lowered[key.lower()]
    return None


def _month_sort_value(value: Any) -> int:
    text = str(value or "").strip()
    month_map = {
        "jan": 1, "january": 1,
        "feb": 2, "february": 2,
        "mar": 3, "march": 3,
        "apr": 4, "april": 4,
        "may": 5,
        "jun": 6, "june": 6,
        "jul": 7, "july": 7,
        "aug": 8, "august": 8,
        "sep": 9, "sept": 9, "september": 9,
        "oct": 10, "october": 10,
        "nov": 11, "november": 11,
        "dec": 12, "december": 12,
    }
    if text.isdigit():
        return int(text)
    return month_map.get(text.lower(), 0)


def _record_sort_key(record: dict[str, Any]) -> tuple[int, int]:
    year = _coerce_int(_find_value(record, "year", "financial_year"), default=0)
    month = _month_sort_value(_find_value(record, "month", "month_name", "monthcode"))
    return (year, month)


def _format_as_of_date(record: dict[str, Any]) -> str | None:
    date_value = _find_value(record, "date", "period", "reference_date")
    if date_value:
        return str(date_value)
    year = _find_value(record, "year", "financial_year")
    month = _find_value(record, "month", "month_name")
    if year and month:
        return f"{month} {year}"
    if year:
        return str(year)
    return None


def _pick_cpi_record(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    filtered = []
    for record in records:
        group = str(_find_value(record, "group", "group_name", "description") or "").lower()
        series = str(_find_value(record, "series", "series_name") or "").lower()
        if "current" in series and ("all groups" in group or "general" in group or not group):
            filtered.append(record)
    if not filtered:
        filtered = records
    return max(filtered, key=_record_sort_key, default=None)


def _pick_latest_record(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    return max(records, key=_record_sort_key, default=None)


def _extract_percent_from_record(record: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _find_value(record, key)
        numeric = _coerce_optional_float(value)
        if numeric is not None:
            return numeric
    return None


async def _request_mospi_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any],
) -> Any:
    last_error: Exception | None = None
    for method in ("GET", "POST"):
        try:
            if method == "GET":
                response = await client.get(url, params=params)
            else:
                response = await client.post(url, params=params)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"MoSPI request failed for {url}: {last_error}")


async def _fetch_cpi_context(client: httpx.AsyncClient) -> tuple[float | None, str | None]:
    payload = await _request_mospi_json(
        client,
        _MOSPI_CPI_URL,
        params={
            "Format": "JSON",
            "Series": "Current_series_2012",
        },
    )
    records = _extract_mospi_records(payload)
    record = _pick_cpi_record(records)
    if record is None:
        return None, None
    value = _extract_percent_from_record(
        record,
        "inflation_rate",
        "inflation",
        "yoy",
        "year_on_year",
        "group_inflation",
    )
    return value, _format_as_of_date(record)


async def _fetch_iip_context(client: httpx.AsyncClient) -> tuple[float | None, str | None]:
    last_error: Exception | None = None
    for url in _MOSPI_IIP_URL_CANDIDATES:
        try:
            payload = await _request_mospi_json(
                client,
                url,
                params={
                    "Format": "JSON",
                    "base_year": "2011-12",
                    "frequency": "Monthly",
                },
            )
            records = _extract_mospi_records(payload)
            record = _pick_latest_record(records)
            if record is None:
                continue
            value = _extract_percent_from_record(record, "growth_rate", "growth", "growth_percent", "yoy")
            return value, _format_as_of_date(record)
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error
    return None, None


async def _fetch_gdp_context(client: httpx.AsyncClient) -> tuple[float | None, str | None]:
    last_error: Exception | None = None
    for url in _MOSPI_GDP_URL_CANDIDATES:
        try:
            payload = await _request_mospi_json(
                client,
                url,
                params={
                    "Format": "JSON",
                    "base_year": "2022-23",
                    "series": "Current",
                },
            )
            records = _extract_mospi_records(payload)
            record = _pick_latest_record(records)
            if record is None:
                continue
            value = _extract_percent_from_record(record, "growth_rate", "growth", "gdp_growth", "yoy")
            return value, _format_as_of_date(record)
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error
    return None, None


def _holding_market_value(item: dict[str, Any]) -> float:
    current_value = _coerce_float(item.get("current_value"))
    if current_value:
        return current_value
    quantity = _coerce_float(item.get("quantity"))
    last_price = _coerce_float(item.get("last_price"))
    return quantity * last_price


def _extract_holdings_payload(raw_response: Any) -> list[dict[str, Any]]:
    if isinstance(raw_response, list):
        return [item for item in raw_response if isinstance(item, dict)]
    if isinstance(raw_response, dict):
        for key in ("holdings", "data", "items"):
            value = raw_response.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _extract_available_cash(raw_margins: Any) -> float:
    if isinstance(raw_margins, (int, float)):
        return float(raw_margins)
    if not isinstance(raw_margins, dict):
        return 0.0

    candidate_paths = (
        ("equity", "available", "cash"),
        ("equity", "available", "live_balance"),
        ("equity", "available", "opening_balance"),
        ("available", "cash"),
        ("net",),
    )
    for path in candidate_paths:
        node: Any = raw_margins
        for key in path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        if node is not None:
            return _coerce_float(node)
    return 0.0


def _extract_mf_value(raw_mf_holdings: Any) -> float:
    total = 0.0
    for item in _extract_holdings_payload(raw_mf_holdings):
        current_value = _coerce_float(item.get("current_value"))
        if current_value == 0.0:
            current_value = _coerce_float(item.get("last_price")) * _coerce_float(item.get("quantity"))
        total += current_value
    return total


def _normalize_mf_holding(item: dict[str, Any]) -> MFHolding:
    quantity = _coerce_float(item.get("quantity"))
    average_price = _coerce_float(item.get("average_price"))
    last_price = _coerce_float(item.get("last_price"))
    current_value = _coerce_float(item.get("current_value"), default=quantity * last_price)
    pnl = _coerce_float(item.get("pnl"), default=current_value - (average_price * quantity))
    base_value = average_price * quantity
    pnl_pct = _coerce_float(
        item.get("pnl_percentage"),
        default=((pnl / base_value) * 100.0 if base_value else 0.0),
    )
    return MFHolding(
        tradingsymbol=str(item.get("tradingsymbol", item.get("symbol", ""))).upper(),
        fund=str(item.get("fund", item.get("scheme_name", item.get("name", "")))),
        folio=str(item.get("folio", "")),
        quantity=quantity,
        average_price=average_price,
        last_price=last_price,
        current_value=current_value,
        pnl=pnl,
        pnl_pct=pnl_pct,
        scheme_type=str(item.get("scheme_type", item.get("type", ""))),
        plan=str(item.get("plan", "")),
    )


def _parse_target_weights_from_rules(rules_path: Path) -> dict[str, float]:
    if not rules_path.exists():
        return {}

    weights: dict[str, float] = {}
    for raw_line in rules_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if "%" not in line or ":" not in line:
            continue
        lhs, rhs = line.split(":", maxsplit=1)
        symbol = lhs.strip("- *` ").upper()
        pct_text = rhs.split("%", maxsplit=1)[0].strip()
        try:
            weights[symbol] = float(pct_text)
        except ValueError:
            continue
    return weights


def _assign_target_weights(raw_holdings: list[dict[str, Any]], explicit_targets: dict[str, float]) -> dict[str, float]:
    eligible_symbols = [
        str(item.get("tradingsymbol", "")).upper()
        for item in raw_holdings
        if str(item.get("tradingsymbol", "")).upper() not in PASSIVE_INSTRUMENTS
    ]
    default_weight = round(100.0 / len(eligible_symbols), 4) if eligible_symbols else 0.0
    targets: dict[str, float] = {}
    for item in raw_holdings:
        symbol = str(item.get("tradingsymbol", "")).upper()
        if symbol in PASSIVE_INSTRUMENTS:
            targets[symbol] = 0.0
        else:
            targets[symbol] = explicit_targets.get(symbol, default_weight)
    return targets


def _normalize_holding(
    item: dict[str, Any],
    total_portfolio_value: float,
    targets: dict[str, float],
) -> Holding:
    quantity = _coerce_int(item.get("quantity"))
    average_price = _coerce_float(item.get("average_price"))
    last_price = _coerce_float(item.get("last_price"))
    current_value = _coerce_float(item.get("current_value"), default=quantity * last_price)
    pnl = _coerce_float(item.get("pnl"), default=(last_price - average_price) * quantity)
    pnl_pct = _coerce_float(
        item.get("pnl_percentage"),
        default=((pnl / (average_price * quantity)) * 100.0 if average_price and quantity else 0.0),
    )
    symbol = str(item.get("tradingsymbol", "")).upper()
    return Holding(
        tradingsymbol=symbol,
        exchange=str(item.get("exchange", "NSE")).upper(),
        quantity=quantity,
        average_price=average_price,
        last_price=last_price,
        current_value=current_value,
        current_weight_pct=(current_value / total_portfolio_value * 100.0) if total_portfolio_value else 0.0,
        target_weight_pct=targets.get(symbol, 0.0),
        pnl=pnl,
        pnl_pct=pnl_pct,
        instrument_token=_coerce_int(item.get("instrument_token")),
    )


async def kite_get_portfolio(
    kite_client: KiteMCPClient,
    settings: Settings | None = None,
) -> PortfolioSnapshot:
    settings = settings or get_settings()
    explicit_targets = _parse_target_weights_from_rules(Path("skills") / "portfolio_rules.md")

    try:
        raw_holdings, raw_margins, raw_mf_holdings = await asyncio.gather(
            kite_client.call_tool("get_holdings"),
            kite_client.call_tool("get_margins"),
            kite_client.call_tool("get_mf_holdings"),
        )
    except Exception as exc:
        raise ToolExecutionError(
            "Kite MCP not connected or session expired. Start the MCP command configured for Artha, "
            "complete Zerodha login if required, then retry."
        ) from exc

    holdings_payload = _extract_holdings_payload(raw_holdings)
    total_equity_value = sum(_holding_market_value(item) for item in holdings_payload)
    target_weights = _assign_target_weights(holdings_payload, explicit_targets)
    available_cash = _extract_available_cash(raw_margins)
    total_value = total_equity_value + available_cash
    holdings = [
        _normalize_holding(item=item, total_portfolio_value=total_value, targets=target_weights)
        for item in holdings_payload
    ]
    mf_total_value = _extract_mf_value(raw_mf_holdings)
    if mf_total_value:
        logger.info("MF holdings value excluded from rebalancing portfolio total: %.2f", mf_total_value)

    return PortfolioSnapshot(
        fetched_at=datetime.now(timezone.utc),
        total_value=total_value,
        available_cash=available_cash,
        holdings=holdings,
    )


async def kite_get_mf_snapshot(
    kite_client: KiteMCPClient,
    settings: Settings | None = None,
) -> MFSnapshot:
    del settings
    try:
        raw_mf_holdings = await kite_client.call_tool("get_mf_holdings")
    except Exception as exc:
        raise ToolExecutionError("Failed to fetch MF holdings from Kite MCP.") from exc

    payload = _extract_holdings_payload(raw_mf_holdings)
    holdings = [_normalize_mf_holding(item) for item in payload]
    total_value = sum(holding.current_value for holding in holdings)
    return MFSnapshot(
        fetched_at=datetime.now(timezone.utc),
        total_value=total_value,
        holdings=holdings,
    )


async def kite_get_price_history(
    kite_client: KiteMCPClient,
    tradingsymbol: str,
    instrument_token: int,
    days: int = 365,
) -> dict[str, Any]:
    end_date = datetime.now(timezone.utc)
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

    return _map_yfinance_snapshot(ticker, raw_payload)


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


async def get_macro_context() -> MacroContext:
    cache_key = datetime.now(timezone.utc).date().isoformat()
    cached = _MACRO_CONTEXT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    errors: list[str] = []
    latest_dates: list[str] = []

    async def fetch_part(
        label: str,
        fetcher: Any,
    ) -> tuple[float | None, str | None]:
        try:
            result = await fetcher(client)
            return result
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            return None, None

    timeout = httpx.Timeout(20.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        try:
            cpi_task = fetch_part("cpi", _fetch_cpi_context)
            iip_task = fetch_part("iip", _fetch_iip_context)
            gdp_task = fetch_part("gdp", _fetch_gdp_context)
            cpi_result, iip_result, gdp_result = await asyncio.wait_for(
                asyncio.gather(cpi_task, iip_task, gdp_task),
                timeout=60,
            )
        except TimeoutError:
            errors.append("macro_context: timed out after 60 seconds")
            cpi_result = (None, None)
            iip_result = (None, None)
            gdp_result = (None, None)

    for _, as_of_date in (cpi_result, iip_result, gdp_result):
        if as_of_date:
            latest_dates.append(as_of_date)

    macro_context = MacroContext(
        cpi_headline_yoy=cpi_result[0],
        iip_growth_latest=iip_result[0],
        gdp_growth_latest=gdp_result[0],
        as_of_date=max(latest_dates) if latest_dates else None,
        fetch_errors=errors,
    )
    _MACRO_CONTEXT_CACHE[cache_key] = macro_context
    return macro_context


def _artifact_path(settings: Settings, *parts: str) -> Path:
    path = settings.kite_data_dir.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def save_kite_artifact(
    payload: dict[str, Any],
    *,
    settings: Settings | None = None,
    category: str,
    stem: str,
) -> Path:
    settings = settings or get_settings()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    artifact = _artifact_path(settings, category, f"{timestamp}_{stem}.json")
    artifact.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")

    latest_artifact = _artifact_path(settings, category, f"latest_{stem}.json")
    latest_artifact.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return artifact


def extract_auth_url(payload: Any) -> str | None:
    if isinstance(payload, str):
        match = re.search(r"https?://\S+", payload)
        return match.group(0).rstrip(").,]}>") if match else None
    if isinstance(payload, list):
        for item in payload:
            found = extract_auth_url(item)
            if found:
                return found
        return None
    if isinstance(payload, dict):
        preferred_keys = (
            "url",
            "login_url",
            "auth_url",
            "authorize_url",
            "redirect_url",
        )
        for key in preferred_keys:
            value = payload.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value.rstrip(").,]}>")
        for value in payload.values():
            found = extract_auth_url(value)
            if found:
                return found
    return None


async def kite_login(
    kite_client: KiteMCPClient,
    settings: Settings | None = None,
) -> tuple[dict[str, Any], str | None, Path]:
    settings = settings or get_settings()
    raw_response = await kite_client.call_tool("login")
    payload = raw_response if isinstance(raw_response, dict) else {"raw_text": raw_response}
    auth_url = extract_auth_url(payload)
    if auth_url:
        payload["auth_url"] = auth_url
    artifact = save_kite_artifact(payload, settings=settings, category="auth", stem="login")
    return payload, auth_url, artifact


async def kite_get_profile(kite_client: KiteMCPClient) -> dict[str, Any]:
    raw_response = await kite_client.call_tool("get_profile")
    if isinstance(raw_response, dict):
        return raw_response
    return {"raw_text": raw_response}


def profile_requires_login(profile: dict[str, Any]) -> bool:
    if not profile:
        return True
    raw_text = str(profile.get("raw_text", "")).lower()
    if "please log in first" in raw_text or "login tool" in raw_text:
        return True
    auth_markers = ("user_id", "user_name", "email", "broker", "exchanges")
    return not any(marker in profile for marker in auth_markers)


async def wait_for_kite_login(
    kite_client: KiteMCPClient,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    deadline = time.monotonic() + settings.kite_login_timeout_seconds

    while time.monotonic() < deadline:
        profile = await kite_get_profile(kite_client)
        if not profile_requires_login(profile):
            return profile
        await asyncio.sleep(settings.kite_login_poll_interval_seconds)

    raise ToolExecutionError(
        "Kite login did not complete before timeout. Finish the browser login and retry."
    )


def get_tool_definitions(settings: Settings | None = None) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    return [
        {
            "name": "kite_get_portfolio",
            "description": (
                "Fetch the live Zerodha/Kite portfolio snapshot through the Kite MCP server configured for Artha. "
                "This returns current equity holdings, cash, and total portfolio value. Use this first for "
                "portfolio runs and before computing any rebalancing recommendation."
            ),
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
        {
            "name": "kite_get_price_history",
            "description": (
                "Fetch daily historical price data for a holding from Artha's Kite MCP server and summarize 52-week "
                "range and 1-year performance. Use this when you need price context for a stock already present "
                "in the live portfolio."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "tradingsymbol": {"type": "string"},
                    "instrument_token": {"type": "integer"},
                    "days": {"type": "integer", "default": 365},
                },
                "required": ["tradingsymbol", "instrument_token"],
            },
        },
        get_tavily_search_tool_definition(settings),
    ]


async def execute_tool_call(
    name: str,
    tool_input: dict[str, Any],
    kite_client: KiteMCPClient,
    settings: Settings | None = None,
) -> tuple[str, bool, PortfolioSnapshot | None]:
    settings = settings or get_settings()
    started = time.perf_counter()
    snapshot: PortfolioSnapshot | None = None
    try:
        if name == "kite_get_portfolio":
            snapshot = await kite_get_portfolio(kite_client, settings=settings)
            payload = snapshot.model_dump(mode="json")
        elif name == "kite_get_price_history":
            payload = await kite_get_price_history(
                kite_client,
                tradingsymbol=str(tool_input["tradingsymbol"]),
                instrument_token=int(tool_input["instrument_token"]),
                days=int(tool_input.get("days", 365)),
            )
        elif name == "tavily_search":
            payload = {
                "result": tavily_search(
                    query=str(tool_input["query"]),
                    max_results=int(tool_input.get("max_results", DEFAULT_TAVILY_MAX_RESULTS)),
                    settings=settings,
                )
            }
        else:
            raise ToolExecutionError(f"Unknown tool requested: {name}")

        return json.dumps(payload, ensure_ascii=True), False, snapshot
    except Exception as exc:
        logger.exception("Tool call failed: %s", name)
        return json.dumps({"error": str(exc)}, ensure_ascii=True), True, snapshot
    finally:
        elapsed = time.perf_counter() - started
        logger.info("Tool call completed: %s in %.2fs", name, elapsed)
