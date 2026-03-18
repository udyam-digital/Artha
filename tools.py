from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from asyncio import wait_for
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config import Settings, get_settings
from models import Holding, PortfolioSnapshot
from rebalance import PASSIVE_INSTRUMENTS

logger = logging.getLogger(__name__)


class ToolExecutionError(RuntimeError):
    pass


@dataclass
class MCPServerDefinition:
    transport: str
    url: str | None
    command: str
    args: list[str]
    env: dict[str, str]


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


def _holding_market_value(item: dict[str, Any]) -> float:
    current_value = _coerce_float(item.get("current_value"))
    if current_value:
        return current_value
    quantity = _coerce_float(item.get("quantity"))
    last_price = _coerce_float(item.get("last_price"))
    return quantity * last_price


def load_kite_server_definition(settings: Settings | None = None) -> MCPServerDefinition:
    settings = settings or get_settings()
    if settings.kite_mcp_url.strip():
        return MCPServerDefinition(
            transport="http",
            url=settings.kite_mcp_url,
            command="",
            args=[],
            env={},
        )

    if not settings.kite_mcp_command.strip():
        raise ToolExecutionError(
            "Kite MCP is not configured for Artha. Set KITE_MCP_URL or KITE_MCP_COMMAND in .env."
        )

    return MCPServerDefinition(
        transport="stdio",
        url=None,
        command=settings.kite_mcp_command,
        args=settings.kite_mcp_args,
        env=settings.kite_mcp_env_json,
    )


class KiteMCPClient:
    def __init__(self, definition: MCPServerDefinition, timeout_seconds: int = 30):
        self.definition = definition
        self.timeout_seconds = timeout_seconds
        self._stack = AsyncExitStack()
        self._session = None

    async def __aenter__(self) -> "KiteMCPClient":
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:
            raise ToolExecutionError(
                "The 'mcp' package is required for Kite MCP access. Install requirements first."
            ) from exc

        try:
            if self.definition.transport == "http":
                read, write, _ = await self._stack.enter_async_context(
                    streamable_http_client(self.definition.url or "")
                )
            else:
                env = dict(os.environ)
                env.update(self.definition.env)
                server_params = StdioServerParameters(
                    command=self.definition.command,
                    args=self.definition.args,
                    env=env,
                )
                read, write = await wait_for(
                    self._stack.enter_async_context(stdio_client(server_params)),
                    timeout=self.timeout_seconds,
                )
            self._session = await wait_for(
                self._stack.enter_async_context(ClientSession(read, write)),
                timeout=self.timeout_seconds,
            )
            await wait_for(self._session.initialize(), timeout=self.timeout_seconds)
        except Exception as exc:
            try:
                await self._stack.aclose()
            except Exception:
                logger.debug("Ignoring MCP cleanup error after initialization failure", exc_info=True)
            raise ToolExecutionError(
                "Failed to initialize Kite MCP for Artha. Verify KITE_MCP_URL or KITE_MCP_COMMAND, "
                "network access, and your Zerodha session."
            ) from exc
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            await self._stack.aclose()
        except Exception as close_exc:
            if self.definition.transport == "http":
                logger.debug("Ignoring streamable HTTP shutdown bug during Kite MCP close", exc_info=True)
                return
            raise

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        if self._session is None:
            raise ToolExecutionError("Kite MCP client is not connected.")

        result = await self._session.call_tool(name, arguments or {})
        if getattr(result, "structuredContent", None) is not None:
            return result.structuredContent

        content = []
        for block in getattr(result, "content", []):
            text = getattr(block, "text", None)
            if text is not None:
                content.append(text)
        if not content:
            return {}

        joined = "\n".join(content).strip()
        try:
            return json.loads(joined)
        except json.JSONDecodeError:
            return {"raw_text": joined}


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
        logger.warning("Price history fetch failed for %s: %s", tradingsymbol, exc)
        return {
            "tradingsymbol": tradingsymbol,
            "error": str(exc),
        }

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
        return {
            "tradingsymbol": tradingsymbol,
            "error": "No historical data available",
        }

    closes = [row["close"] for row in parsed if row["close"] > 0]
    highs = [row["high"] for row in parsed if row["high"] > 0]
    lows = [row["low"] for row in parsed if row["low"] > 0]
    current_price = closes[-1] if closes else 0.0
    price_1y_ago = closes[0] if closes else 0.0
    high_52w = max(highs) if highs else 0.0
    low_52w = min(lows) if lows else 0.0

    return {
        "tradingsymbol": tradingsymbol,
        "52w_high": high_52w,
        "52w_low": low_52w,
        "current_vs_52w_high_pct": ((current_price / high_52w) - 1) * 100.0 if high_52w else 0.0,
        "price_1y_ago": price_1y_ago,
        "price_change_1y_pct": ((current_price / price_1y_ago) - 1) * 100.0 if price_1y_ago else 0.0,
    }


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


def save_portfolio_snapshot(snapshot: PortfolioSnapshot, settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return save_kite_artifact(
        snapshot.model_dump(mode="json"),
        settings=settings,
        category="portfolio",
        stem="snapshot",
    )


def get_tool_definitions(settings: Settings | None = None) -> list[dict[str, Any]]:
    del settings
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
        {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 12,
            "user_location": {
                "type": "approximate",
                "city": "Bengaluru",
                "region": "Karnataka",
                "country": "IN",
                "timezone": "Asia/Kolkata",
            },
        },
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
        else:
            raise ToolExecutionError(f"Unknown tool requested: {name}")

        return json.dumps(payload, ensure_ascii=True), False, snapshot
    except Exception as exc:
        logger.exception("Tool call failed: %s", name)
        return json.dumps({"error": str(exc)}, ensure_ascii=True), True, snapshot
    finally:
        elapsed = time.perf_counter() - started
        logger.info("Tool call completed: %s in %.2fs", name, elapsed)
