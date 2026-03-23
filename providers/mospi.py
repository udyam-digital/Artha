"""MoSPI MCP client — fetches CPI, IIP, and GDP macro context via the MoSPI MCP server."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from kite.client import MCPServerDefinition, MCPToolClient
from models import MacroContext

logger = logging.getLogger(__name__)

DEFAULT_MOSPI_MCP_URL = "https://datainnovation.mospi.gov.in/mcp"

# Pre-verified filter codes — confirmed via MoSPI MCP step3/step4 exploration.
# Do not change without re-running step3_get_metadata to re-verify codes.
_CPI_FILTERS: dict[str, Any] = {
    "base_year": "2012",
    "series": "Current",
    "state_code": "99",  # All India
    "group_code": "0",  # General index
    "sector_code": "3",  # Combined (Rural + Urban)
    "Format": "JSON",
    "limit": 1,
}

_IIP_FILTERS: dict[str, Any] = {
    "base_year": "2011-12",
    "type": "General",
    "category_code": "4",  # General IIP index (overall)
    "Format": "JSON",
    "limit": 1,
}

_NAS_GDP_FILTERS: dict[str, Any] = {
    "base_year": "2022-23",
    "series": "Current",
    "frequency_code": "Quarterly",
    "indicator_code": "22",  # GDP Growth Rate
    "Format": "JSON",
    "limit": 1,
}


def _parse_cpi_response(payload: dict[str, Any]) -> tuple[float | None, str | None]:
    """Extract headline CPI YoY inflation (%) and as-of date."""
    data = payload.get("data", [])
    if not isinstance(data, list) or not data:
        return None, None
    record = data[0]
    try:
        value = float(record["inflation"]) if record.get("inflation") is not None else None
    except (TypeError, ValueError):
        value = None
    month = record.get("month")
    year = record.get("year")
    as_of = f"{month} {year}" if month and year else str(year) if year else None
    return value, as_of


def _parse_iip_response(payload: dict[str, Any]) -> tuple[float | None, str | None]:
    """Extract IIP general growth rate (%) and fiscal year."""
    data = payload.get("data", [])
    if not isinstance(data, list) or not data:
        return None, None
    record = data[0]
    try:
        value = float(record["growth_rate"]) if record.get("growth_rate") is not None else None
    except (TypeError, ValueError):
        value = None
    as_of = str(record["year"]) if record.get("year") else None
    return value, as_of


def _parse_gdp_response(payload: dict[str, Any]) -> tuple[float | None, str | None]:
    """Extract GDP growth rate at constant prices (%) and quarter label."""
    data = payload.get("data", [])
    if not isinstance(data, list) or not data:
        return None, None
    record = data[0]
    try:
        value = float(record["constant_price"]) if record.get("constant_price") is not None else None
    except (TypeError, ValueError):
        value = None
    year = record.get("year")
    quarter = record.get("quarter")
    as_of = f"{quarter} {year}" if quarter and year else str(year) if year else None
    return value, as_of


async def get_macro_context_via_mcp(
    mospi_mcp_url: str = DEFAULT_MOSPI_MCP_URL,
    timeout_seconds: int = 30,
) -> MacroContext:
    """Fetch CPI inflation, IIP growth, and GDP growth from the MoSPI MCP server.

    Calls step4_get_data on CPI, IIP, and NAS datasets in parallel using
    pre-verified filter codes. Returns a MacroContext; per-dataset failures are
    captured in fetch_errors rather than raised.
    """
    definition = MCPServerDefinition(
        transport="http",
        url=mospi_mcp_url,
        command="",
        args=[],
        env={},
    )
    errors: list[str] = []
    dates: list[str] = []

    async with MCPToolClient(definition, timeout_seconds=timeout_seconds) as client:

        async def _fetch(
            label: str,
            dataset: str,
            filters: dict[str, Any],
            parser: Any,
        ) -> tuple[float | None, str | None]:
            try:
                payload = await client.call_tool("step4_get_data", {"dataset": dataset, "filters": filters})
                return parser(payload)
            except Exception as exc:
                errors.append(f"{label}: {exc}")
                logger.warning("[mospi] %s fetch failed: %s", label, exc)
                return None, None

        results = await asyncio.gather(
            _fetch("cpi", "CPI", _CPI_FILTERS, _parse_cpi_response),
            _fetch("iip", "IIP", _IIP_FILTERS, _parse_iip_response),
            _fetch("gdp", "NAS", _NAS_GDP_FILTERS, _parse_gdp_response),
        )

    (cpi_value, cpi_date), (iip_value, iip_date), (gdp_value, gdp_date) = results
    for d in (cpi_date, iip_date, gdp_date):
        if d:
            dates.append(d)

    return MacroContext(
        cpi_headline_yoy=cpi_value,
        iip_growth_latest=iip_value,
        gdp_growth_latest=gdp_value,
        as_of_date=max(dates) if dates else None,
        fetch_errors=errors,
    )
