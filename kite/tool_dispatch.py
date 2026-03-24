from __future__ import annotations

import json
import logging
import time
from typing import Any

from config import Settings, get_settings
from kite.portfolio import kite_get_portfolio
from kite.price import kite_get_price_history
from models import PortfolioSnapshot
from providers.kite import KiteMCPClient
from providers.mcp_client import ToolExecutionError
from providers.tavily import DEFAULT_TAVILY_MAX_RESULTS, get_tavily_search_tool_definition, tavily_search

logger = logging.getLogger(__name__)


def get_tool_definitions(settings: Settings | None = None) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    return [
        {
            "name": "kite_get_portfolio",
            "description": "Fetch the live Zerodha/Kite portfolio snapshot through the Kite MCP server configured for Artha. This returns current equity holdings, cash, and total portfolio value. Use this first for portfolio runs and before computing any rebalancing recommendation.",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "kite_get_price_history",
            "description": "Fetch daily historical price data for a holding from Artha's Kite MCP server and summarize 52-week range and 1-year performance. Use this when you need price context for a stock already present in the live portfolio.",
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
        logger.info("Tool call completed: %s in %.2fs", name, time.perf_counter() - started)
