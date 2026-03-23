"""MoSPI MCP client — fetches macro context via the esankhyiki MCP server at mcp.mospi.gov.in."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from models import MacroContext
from providers.mcp_client import MCPServerDefinition, MCPToolClient

logger = logging.getLogger(__name__)

DEFAULT_MOSPI_MCP_URL = "https://mcp.mospi.gov.in/"

# ── CPI: All India, General index, Combined (Rural+Urban), base year 2012 ─────
_CPI_FILTERS: dict[str, Any] = {
    "base_year": "2012",
    "series": "Current",
    "state_code": 99,  # All India
    "group_code": 0,  # General
    "sector_code": 3,  # Combined (Rural + Urban)
    "year": 2025,
    "Format": "JSON",
    "limit": 1,
}

# ── IIP General: overall industrial production growth rate ────────────────────
_IIP_GENERAL_FILTERS: dict[str, Any] = {
    "base_year": "2011-12",
    "type": "General",
    "category_code": 4,  # General IIP
    "Format": "JSON",
    "limit": 1,
}

# ── IIP Capital Goods: capex/defence proxy ────────────────────────────────────
_IIP_CAPITAL_GOODS_FILTERS: dict[str, Any] = {
    "base_year": "2011-12",
    "type": "Use-based category",
    "category_code": 6,  # Capital Goods
    "Format": "JSON",
    "limit": 1,
}

# ── NAS GDP: quarterly growth rate at constant prices ─────────────────────────
_GDP_FILTERS: dict[str, Any] = {
    "base_year": "2022-23",
    "series": "Current",
    "frequency_code": 2,  # Quarterly
    "indicator_code": 22,  # GDP Growth Rate
    "year": "2024-25",
    "Format": "JSON",
    "limit": 4,
}


def _parse_cpi(payload: Any) -> tuple[float | None, str | None]:
    data = payload.get("data", []) if isinstance(payload, dict) else []
    if not data:
        return None, None
    record = data[0]
    try:
        value = float(record["inflation"])
    except (KeyError, TypeError, ValueError):
        value = None
    month = record.get("month")
    year = record.get("year")
    as_of = f"{month} {year}" if month and year else str(year) if year else None
    return value, as_of


def _parse_iip(payload: Any) -> tuple[float | None, str | None]:
    data = payload.get("data", []) if isinstance(payload, dict) else []
    if not data:
        return None, None
    record = data[0]
    try:
        value = float(record["growth_rate"])
    except (KeyError, TypeError, ValueError):
        value = None
    as_of = record.get("year")
    return value, str(as_of) if as_of else None


def _parse_gdp(payload: Any) -> tuple[float | None, str | None]:
    """Pick the latest quarter with a non-null constant_price growth rate."""
    data = payload.get("data", []) if isinstance(payload, dict) else []
    if not data:
        return None, None
    # Data comes ordered Q1→Q4; walk in reverse to find latest populated quarter
    for record in reversed(data):
        year = record.get("year", "")
        quarter = record.get("quarter", "")
        as_of = f"{quarter} {year}" if quarter and year else str(year) if year else None
        try:
            value = float(record["constant_price"])
            return value, as_of
        except (KeyError, TypeError, ValueError):
            if as_of:
                return None, as_of
            continue
    return None, None


# Aliases for backwards compatibility with tests and external callers
_parse_cpi_response = _parse_cpi
_parse_iip_response = _parse_iip
_parse_gdp_response = _parse_gdp


async def get_macro_context_via_mcp(
    mospi_mcp_url: str = DEFAULT_MOSPI_MCP_URL,
    timeout_seconds: int = 30,
) -> MacroContext:
    """Fetch CPI, IIP (General + Capital Goods), GDP, and unemployment from MoSPI MCP.

    Uses the esankhyiki MCP server at mcp.mospi.gov.in. All datasets fetched in
    parallel; per-dataset failures are captured in fetch_errors rather than raised.
    """
    definition = MCPServerDefinition(
        transport="http",
        url=mospi_mcp_url,
        command="",
        args=[],
        env={},
    )
    errors: list[str] = []

    async with MCPToolClient(definition, timeout_seconds=timeout_seconds) as client:

        async def _fetch(
            label: str, dataset: str, filters: dict[str, Any], parser: Any
        ) -> tuple[float | None, str | None]:
            try:
                payload = await client.call_tool("step4_get_data", {"dataset": dataset, "filters": filters})
                return parser(payload)
            except Exception as exc:
                errors.append(f"{label}: {exc}")
                logger.warning("[mospi] %s fetch failed: %s", label, exc)
                return None, None

        results = await asyncio.gather(
            _fetch("cpi", "CPI", _CPI_FILTERS, _parse_cpi),
            _fetch("iip_general", "IIP", _IIP_GENERAL_FILTERS, _parse_iip),
            _fetch("iip_capital_goods", "IIP", _IIP_CAPITAL_GOODS_FILTERS, _parse_iip),
            _fetch("gdp", "NAS", _GDP_FILTERS, _parse_gdp),
        )

    (cpi_val, cpi_date), (iip_val, iip_date), (capex_val, _), (gdp_val, gdp_date) = results

    dates = [d for d in (cpi_date, iip_date, gdp_date) if d]

    return MacroContext(
        cpi_headline_yoy=cpi_val,
        cpi_as_of=cpi_date,
        iip_growth_latest=iip_val,
        iip_capital_goods_growth=capex_val,
        iip_as_of=iip_date,
        gdp_growth_latest=gdp_val,
        gdp_as_of=gdp_date,
        as_of_date=dates[0] if dates else None,
        fetch_errors=errors,
    )
