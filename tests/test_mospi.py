"""Tests for mcp/mospi.py — MoSPI MCP macro context client."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models import MacroContext
from providers.mospi import (
    _parse_cpi_response,
    _parse_gdp_response,
    _parse_iip_response,
    get_macro_context_via_mcp,
)

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Parser unit tests (sync — no mark needed)
# ---------------------------------------------------------------------------


class TestParseCpiResponse:
    def test_happy_path(self) -> None:
        payload = {
            "data": [
                {
                    "baseyear": "2012",
                    "year": 2025,
                    "month": "December",
                    "state": "All India",
                    "sector": "Combined",
                    "group": "General",
                    "inflation": "1.33",
                    "status": "F",
                }
            ]
        }
        value, as_of = _parse_cpi_response(payload)
        assert value == pytest.approx(1.33)
        assert as_of == "December 2025"

    def test_empty_data_list(self) -> None:
        value, as_of = _parse_cpi_response({"data": []})
        assert value is None
        assert as_of is None

    def test_missing_data_key(self) -> None:
        value, as_of = _parse_cpi_response({})
        assert value is None
        assert as_of is None

    def test_non_numeric_inflation(self) -> None:
        payload = {"data": [{"inflation": "n/a", "month": "Jan", "year": 2025}]}
        value, as_of = _parse_cpi_response(payload)
        assert value is None
        assert as_of == "Jan 2025"

    def test_none_inflation(self) -> None:
        payload = {"data": [{"inflation": None, "month": "Jan", "year": 2025}]}
        value, as_of = _parse_cpi_response(payload)
        assert value is None

    def test_as_of_year_only_when_no_month(self) -> None:
        payload = {"data": [{"inflation": "2.5", "year": 2024}]}
        value, as_of = _parse_cpi_response(payload)
        assert value == pytest.approx(2.5)
        assert as_of == "2024"


class TestParseIipResponse:
    def test_happy_path(self) -> None:
        payload = {
            "data": [
                {
                    "base_year": "2011-12",
                    "year": "2024-25",
                    "type": "General",
                    "category": "General",
                    "index": "152.6",
                    "growth_rate": "4.0",
                }
            ]
        }
        value, as_of = _parse_iip_response(payload)
        assert value == pytest.approx(4.0)
        assert as_of == "2024-25"

    def test_empty_data(self) -> None:
        value, as_of = _parse_iip_response({"data": []})
        assert value is None
        assert as_of is None

    def test_non_numeric_growth(self) -> None:
        payload = {"data": [{"growth_rate": "N/A", "year": "2024-25"}]}
        value, as_of = _parse_iip_response(payload)
        assert value is None
        assert as_of == "2024-25"

    def test_missing_year(self) -> None:
        payload = {"data": [{"growth_rate": "5.0"}]}
        value, as_of = _parse_iip_response(payload)
        assert value == pytest.approx(5.0)
        assert as_of is None


class TestParseGdpResponse:
    def test_happy_path(self) -> None:
        payload = {
            "data": [
                {
                    "base_year": "2022-23",
                    "series": "Current",
                    "year": "2025-26",
                    "indicator": "GDP Growth Rate",
                    "frequency": "Quarterly",
                    "quarter": "Q3",
                    "current_price": "8.93",
                    "constant_price": "7.82",
                    "unit": "%",
                }
            ]
        }
        value, as_of = _parse_gdp_response(payload)
        assert value == pytest.approx(7.82)
        assert as_of == "Q3 2025-26"

    def test_empty_data(self) -> None:
        value, as_of = _parse_gdp_response({"data": []})
        assert value is None
        assert as_of is None

    def test_non_numeric_constant_price(self) -> None:
        payload = {"data": [{"constant_price": "N/A", "year": "2025-26", "quarter": "Q1"}]}
        value, as_of = _parse_gdp_response(payload)
        assert value is None
        assert as_of == "Q1 2025-26"

    def test_year_only_when_no_quarter(self) -> None:
        payload = {"data": [{"constant_price": "6.5", "year": "2024-25"}]}
        value, as_of = _parse_gdp_response(payload)
        assert value == pytest.approx(6.5)
        assert as_of == "2024-25"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MOCK_CPI_PAYLOAD = {"data": [{"inflation": "1.33", "month": "December", "year": 2025}], "statusCode": True}
_MOCK_IIP_PAYLOAD = {"data": [{"growth_rate": "4.0", "year": "2024-25"}], "statusCode": True}
_MOCK_GDP_PAYLOAD = {"data": [{"constant_price": "7.82", "year": "2025-26", "quarter": "Q3"}], "statusCode": True}


def _make_mock_client(responses: dict[str, Any] | None = None):
    """Build a mock MCPToolClient that returns preset payloads keyed by dataset."""
    _responses = responses or {"CPI": _MOCK_CPI_PAYLOAD, "IIP": _MOCK_IIP_PAYLOAD, "NAS": _MOCK_GDP_PAYLOAD}

    async def fake_call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        dataset = arguments.get("dataset", "")
        return _responses.get(dataset, {})

    mock = MagicMock()
    mock.call_tool = AsyncMock(side_effect=fake_call_tool)
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    return mock


# ---------------------------------------------------------------------------
# get_macro_context_via_mcp integration tests (mocked MCPToolClient)
# ---------------------------------------------------------------------------


async def test_get_macro_context_via_mcp_all_success() -> None:
    with patch("providers.mospi.MCPToolClient", return_value=_make_mock_client()):
        result = await get_macro_context_via_mcp()

    assert isinstance(result, MacroContext)
    assert result.cpi_headline_yoy == pytest.approx(1.33)
    assert result.iip_growth_latest == pytest.approx(4.0)
    assert result.gdp_growth_latest == pytest.approx(7.82)
    assert result.fetch_errors == []
    assert result.as_of_date is not None


async def test_get_macro_context_via_mcp_partial_failure() -> None:
    """IIP fetch fails — others succeed, error captured in fetch_errors."""

    async def fake_call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        ds = arguments.get("dataset", "")
        if ds == "CPI":
            return _MOCK_CPI_PAYLOAD
        if ds == "IIP":
            raise RuntimeError("IIP MCP timeout")
        return _MOCK_GDP_PAYLOAD

    mock = MagicMock()
    mock.call_tool = AsyncMock(side_effect=fake_call_tool)
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)

    with patch("providers.mospi.MCPToolClient", return_value=mock):
        result = await get_macro_context_via_mcp()

    assert result.cpi_headline_yoy == pytest.approx(1.33)
    assert result.iip_growth_latest is None
    assert result.gdp_growth_latest == pytest.approx(7.82)
    assert len(result.fetch_errors) == 2
    assert all("iip" in e for e in result.fetch_errors)


async def test_get_macro_context_via_mcp_all_fail() -> None:
    """All datasets fail — returns empty MacroContext with 3 errors."""
    mock = MagicMock()
    mock.call_tool = AsyncMock(side_effect=RuntimeError("MCP server unreachable"))
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)

    with patch("providers.mospi.MCPToolClient", return_value=mock):
        result = await get_macro_context_via_mcp()

    assert result.cpi_headline_yoy is None
    assert result.iip_growth_latest is None
    assert result.gdp_growth_latest is None
    assert len(result.fetch_errors) == 4


async def test_get_macro_context_via_mcp_empty_data_list() -> None:
    """MCP returns success but empty data list — all values None, no errors."""
    with patch(
        "providers.mospi.MCPToolClient",
        return_value=_make_mock_client({"CPI": {"data": []}, "IIP": {"data": []}, "NAS": {"data": []}}),
    ):
        result = await get_macro_context_via_mcp()

    assert result.cpi_headline_yoy is None
    assert result.iip_growth_latest is None
    assert result.gdp_growth_latest is None
    assert result.fetch_errors == []


# ---------------------------------------------------------------------------
# get_macro_context fallback tests (kite/tools.py)
# ---------------------------------------------------------------------------


async def test_get_macro_context_uses_mcp_primary(monkeypatch) -> None:
    """get_macro_context() returns MoSPI MCP result when MCP succeeds."""
    import kite.tools as kite_tools

    monkeypatch.setattr(kite_tools, "_MACRO_CONTEXT_CACHE", {})

    expected = MacroContext(
        cpi_headline_yoy=1.33,
        iip_growth_latest=4.0,
        gdp_growth_latest=7.82,
        as_of_date="December 2025",
        fetch_errors=[],
    )

    mock_mcp_module = MagicMock()
    mock_mcp_module.get_macro_context_via_mcp = AsyncMock(return_value=expected)

    with (
        patch.dict("sys.modules", {"providers.mospi": mock_mcp_module}),
        patch("kite.tools.get_settings") as mock_settings,
    ):
        mock_settings.return_value = MagicMock(
            mospi_mcp_url="https://datainnovation.mospi.gov.in/mcp",
            mospi_mcp_timeout_seconds=30,
        )
        result = await kite_tools.get_macro_context()

    assert result.cpi_headline_yoy == pytest.approx(1.33)
    assert result.iip_growth_latest == pytest.approx(4.0)
    assert result.gdp_growth_latest == pytest.approx(7.82)


async def test_get_macro_context_returns_error_context_on_mcp_failure(monkeypatch) -> None:
    """get_macro_context() returns empty macro context with fetch_errors when MoSPI MCP raises."""
    import kite.tools as kite_tools

    monkeypatch.setattr(kite_tools, "_MACRO_CONTEXT_CACHE", {})

    mock_mcp_module = MagicMock()
    mock_mcp_module.get_macro_context_via_mcp = AsyncMock(side_effect=RuntimeError("MCP unreachable"))

    with (
        patch.dict("sys.modules", {"providers.mospi": mock_mcp_module}),
        patch("kite.tools.get_settings") as mock_settings,
    ):
        mock_settings.return_value = MagicMock(
            mospi_mcp_url="https://datainnovation.mospi.gov.in/mcp",
            mospi_mcp_timeout_seconds=30,
        )
        result = await kite_tools.get_macro_context()

    assert result.cpi_headline_yoy is None
    assert result.iip_growth_latest is None
    assert result.gdp_growth_latest is None
    assert result.fetch_errors == ["mospi_mcp: MCP unreachable"]
