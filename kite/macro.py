from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from config import get_settings
from models import MacroContext
from providers.mcp_client import MCPServerDefinition, MCPToolClient

logger = logging.getLogger(__name__)

_MACRO_CONTEXT_CACHE: dict[str, MacroContext] = {}

_CPI_FILTERS: dict[str, Any] = {
    "base_year": "2012",
    "series": "Current",
    "state_code": 99,
    "group_code": 0,
    "sector_code": 3,
    "year": 2025,
    "Format": "JSON",
    "limit": 1,
}

_IIP_GENERAL_FILTERS: dict[str, Any] = {
    "base_year": "2011-12",
    "type": "General",
    "category_code": 4,
    "Format": "JSON",
    "limit": 1,
}

_GDP_FILTERS: dict[str, Any] = {
    "base_year": "2022-23",
    "series": "Current",
    "frequency_code": 2,
    "indicator_code": 22,
    "year": "2024-25",
    "Format": "JSON",
    "limit": 4,
}


def _extract_mospi_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "records", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return [payload] if payload else []


def _find_value(record: dict[str, Any], *keys: str) -> Any:
    normalized = {str(key).lower().replace("_", "").replace(" ", ""): value for key, value in record.items()}
    for key in keys:
        value = normalized.get(key.lower().replace("_", "").replace(" ", ""))
        if value not in (None, ""):
            return value
    return None


def _coerce_float(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _record_as_of(record: dict[str, Any]) -> str | None:
    month = _find_value(record, "month")
    quarter = _find_value(record, "quarter")
    year = _find_value(record, "year", "as_of_date", "asofdate")
    if month and year:
        return f"{month} {year}"
    if quarter and year:
        return f"{quarter} {year}"
    if month:
        return str(month)
    if year:
        return str(year)
    return None


async def _call_mospi(dataset: str, filters: dict[str, Any]) -> Any:
    settings = get_settings()
    definition = MCPServerDefinition(
        transport="http",
        url=settings.mospi_mcp_url,
        command="",
        args=[],
        env={},
    )
    async with MCPToolClient(definition, timeout_seconds=settings.mospi_mcp_timeout_seconds) as client:
        return await client.call_tool("step4_get_data", {"dataset": dataset, "filters": filters})


async def _fetch_cpi_context(_client: Any = None) -> tuple[float | None, str | None]:
    payload = await _call_mospi("CPI", _CPI_FILTERS)
    records = _extract_mospi_records(payload)
    if not records:
        return None, None
    record = records[0]
    return _coerce_float(_find_value(record, "inflation", "general_index", "generalindex", "value")), _record_as_of(
        record
    )


async def _fetch_iip_context(_client: Any = None) -> tuple[float | None, str | None]:
    payload = await _call_mospi("IIP", _IIP_GENERAL_FILTERS)
    records = _extract_mospi_records(payload)
    if not records:
        return None, None
    record = records[0]
    return _coerce_float(_find_value(record, "growth_rate", "growthrate", "value")), _record_as_of(record)


async def _fetch_gdp_context(_client: Any = None) -> tuple[float | None, str | None]:
    payload = await _call_mospi("NAS", _GDP_FILTERS)
    records = _extract_mospi_records(payload)
    if not records:
        return None, None
    for record in reversed(records):
        value = _coerce_float(_find_value(record, "constant_price", "constantprice", "growth_rate", "value"))
        as_of = _record_as_of(record)
        if value is not None or as_of:
            return value, as_of
    return None, None


async def get_macro_context() -> MacroContext:
    cache_key = datetime.now(UTC).date().isoformat()
    cached = _MACRO_CONTEXT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    errors: list[str] = []

    async def capture(label: str, fetcher: Any) -> tuple[float | None, str | None]:
        try:
            return await fetcher(None)
        except Exception as exc:
            logger.warning("[macro_context] %s fetch failed: %s", label, exc)
            errors.append(f"{label}: {exc}")
            return None, None

    (cpi_value, cpi_as_of), (iip_value, iip_as_of), (gdp_value, gdp_as_of) = await asyncio.gather(
        capture("cpi", _fetch_cpi_context),
        capture("iip", _fetch_iip_context),
        capture("gdp", _fetch_gdp_context),
    )

    macro_context = MacroContext(
        cpi_headline_yoy=cpi_value,
        cpi_as_of=cpi_as_of,
        iip_growth_latest=iip_value,
        iip_as_of=iip_as_of,
        gdp_growth_latest=gdp_value,
        gdp_as_of=gdp_as_of,
        as_of_date=next((value for value in (cpi_as_of, iip_as_of, gdp_as_of) if value), None),
        fetch_errors=errors,
    )
    _MACRO_CONTEXT_CACHE[cache_key] = macro_context
    return macro_context


__all__ = [
    "_MACRO_CONTEXT_CACHE",
    "_extract_mospi_records",
    "_fetch_cpi_context",
    "_fetch_gdp_context",
    "_fetch_iip_context",
    "_find_value",
    "get_macro_context",
]
