import kite.macro as _macro_module
import kite.provider_payloads as _provider_payloads_module
import kite.yfinance_tools as _yfinance_tools_module
from config import Settings, get_settings
from kite.coerce import (
    _coerce_float,
    _coerce_int,
    _coerce_json_object,
    _coerce_optional_float,
    _coerce_optional_int,
    _coerce_percent,
)
from kite.macro import (
    _MACRO_CONTEXT_CACHE,
    _extract_mospi_records,
    _fetch_cpi_context,
    _fetch_gdp_context,
    _fetch_iip_context,
    _find_value,
)
from kite.portfolio import _holding_market_value, kite_get_mf_snapshot, kite_get_portfolio
from kite.price import _extract_holdings_payload, kite_get_price_history
from kite.session import (
    extract_auth_url,
    kite_get_profile,
    kite_login,
    profile_requires_login,
    save_kite_artifact,
    wait_for_kite_login,
)
from kite.tool_dispatch import execute_tool_call, get_tool_definitions
from kite.yfinance_tools import _is_missing_company_response, _normalize_yfinance_ticker, map_yfinance_snapshot
from providers.mcp_client import MCPToolClient, ToolExecutionError
from providers.nse import load_nse_server_definition
from providers.yfinance import load_yfinance_server_definition

_DEFAULT_FETCH_CPI_CONTEXT = _fetch_cpi_context
_DEFAULT_FETCH_IIP_CONTEXT = _fetch_iip_context
_DEFAULT_FETCH_GDP_CONTEXT = _fetch_gdp_context


async def get_macro_context():
    cache_key = __import__("datetime").datetime.now(__import__("datetime").UTC).date().isoformat()
    cached = _MACRO_CONTEXT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    helper_overridden = any(
        current is not default
        for current, default in (
            (_fetch_cpi_context, _DEFAULT_FETCH_CPI_CONTEXT),
            (_fetch_iip_context, _DEFAULT_FETCH_IIP_CONTEXT),
            (_fetch_gdp_context, _DEFAULT_FETCH_GDP_CONTEXT),
        )
    )
    if not helper_overridden:
        try:
            from providers.mospi import get_macro_context_via_mcp

            settings = get_settings()
            result = await get_macro_context_via_mcp(
                mospi_mcp_url=settings.mospi_mcp_url,
                timeout_seconds=settings.mospi_mcp_timeout_seconds,
            )
        except Exception as exc:
            from models import MacroContext

            result = MacroContext(fetch_errors=[f"mospi_mcp: {exc}"])
        _MACRO_CONTEXT_CACHE[cache_key] = result
        return result
    _macro_module._extract_mospi_records = _extract_mospi_records
    _macro_module._fetch_cpi_context = _fetch_cpi_context
    _macro_module._fetch_gdp_context = _fetch_gdp_context
    _macro_module._fetch_iip_context = _fetch_iip_context
    _macro_module._find_value = _find_value
    _macro_module._MACRO_CONTEXT_CACHE = _MACRO_CONTEXT_CACHE
    return await _macro_module.get_macro_context()


async def get_nse_india_provider_payload(ticker: str):
    _provider_payloads_module.get_settings = get_settings
    _provider_payloads_module.load_nse_server_definition = load_nse_server_definition
    _provider_payloads_module.MCPToolClient = MCPToolClient
    return await _provider_payloads_module.get_nse_india_provider_payload(ticker)


async def get_yfinance_snapshot(ticker_ns: str):
    _yfinance_tools_module.get_settings = get_settings
    _yfinance_tools_module.load_yfinance_server_definition = load_yfinance_server_definition
    _yfinance_tools_module.MCPToolClient = MCPToolClient
    _yfinance_tools_module._normalize_yfinance_ticker = _normalize_yfinance_ticker
    return await _yfinance_tools_module.get_yfinance_snapshot(ticker_ns)


async def get_yfinance_company_info(ticker_ns: str):
    _yfinance_tools_module.get_settings = get_settings
    _yfinance_tools_module.load_yfinance_server_definition = load_yfinance_server_definition
    _yfinance_tools_module.MCPToolClient = MCPToolClient
    _yfinance_tools_module._normalize_yfinance_ticker = _normalize_yfinance_ticker
    return await _yfinance_tools_module.get_yfinance_company_info(ticker_ns)


async def get_yfinance_provider_payload(ticker_ns: str):
    ticker = _normalize_yfinance_ticker(ticker_ns)
    raw_company_info = await get_yfinance_company_info(ticker_ns)
    snapshot = await get_yfinance_snapshot(ticker_ns)
    errors = []
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


__all__ = [
    "Settings",
    "ToolExecutionError",
    "_MACRO_CONTEXT_CACHE",
    "_coerce_float",
    "_coerce_int",
    "_coerce_json_object",
    "_coerce_optional_float",
    "_coerce_optional_int",
    "_coerce_percent",
    "_extract_holdings_payload",
    "_extract_mospi_records",
    "_fetch_cpi_context",
    "_fetch_gdp_context",
    "_fetch_iip_context",
    "_find_value",
    "_holding_market_value",
    "_is_missing_company_response",
    "_normalize_yfinance_ticker",
    "MCPToolClient",
    "execute_tool_call",
    "extract_auth_url",
    "get_macro_context",
    "get_nse_india_provider_payload",
    "get_settings",
    "get_tool_definitions",
    "get_yfinance_company_info",
    "get_yfinance_provider_payload",
    "get_yfinance_snapshot",
    "kite_get_mf_snapshot",
    "kite_get_portfolio",
    "kite_get_price_history",
    "kite_get_profile",
    "kite_login",
    "map_yfinance_snapshot",
    "load_nse_server_definition",
    "load_yfinance_server_definition",
    "profile_requires_login",
    "save_kite_artifact",
    "wait_for_kite_login",
]
