from __future__ import annotations

import json

import pytest

import kite.tools as kite_tools
from config import Settings

pytestmark = pytest.mark.anyio


class FakeMCPClient:
    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def call_tool(self, name, arguments=None):
        assert name == "get_stock_info"
        assert arguments == {"ticker": "BSE.NS"}
        return self.payload


def make_settings(tmp_path):
    return Settings(
        ANTHROPIC_API_KEY="test-key",
        REPORTS_DIR=str(tmp_path / "reports"),
        KITE_DATA_DIR=str(tmp_path / "kite"),
    )


async def test_get_yfinance_snapshot_valid(tmp_path, monkeypatch) -> None:
    payload = {
        "result": json.dumps(
            {
                "currentPrice": 2500.0,
                "fiftyTwoWeekLow": 1800.0,
                "fiftyTwoWeekHigh": 3000.0,
                "trailingPE": 45.0,
                "forwardPE": 38.0,
                "priceToBook": 12.0,
                "revenueGrowth": 0.18,
                "earningsGrowth": 0.22,
                "profitMargins": 0.34,
                "numberOfAnalystOpinions": 12,
                "targetMeanPrice": 2750.0,
                "targetMedianPrice": 2700.0,
                "sector": "Financial Services",
                "industry": "Capital Markets",
            }
        )
    }
    monkeypatch.setattr(kite_tools, "get_settings", lambda: make_settings(tmp_path))
    monkeypatch.setattr(kite_tools, "load_yfinance_server_definition", lambda settings: object())
    monkeypatch.setattr(kite_tools, "MCPToolClient", lambda definition, timeout_seconds: FakeMCPClient(payload))

    result = await kite_tools.get_yfinance_snapshot("BSE")

    assert result["cmp"] > 0
    assert result["upside_pct"] is not None


async def test_get_yfinance_snapshot_invalid(tmp_path, monkeypatch) -> None:
    class FailingMCPClient(FakeMCPClient):
        async def call_tool(self, name, arguments=None):
            raise RuntimeError("ticker not found")

    monkeypatch.setattr(kite_tools, "get_settings", lambda: make_settings(tmp_path))
    monkeypatch.setattr(kite_tools, "load_yfinance_server_definition", lambda settings: object())
    monkeypatch.setattr(kite_tools, "MCPToolClient", lambda definition, timeout_seconds: FailingMCPClient({}))

    result = await kite_tools.get_yfinance_snapshot("FAKEXYZ")

    assert result == {}


async def test_get_yfinance_snapshot_fields(tmp_path, monkeypatch) -> None:
    payload = {
        "result": {
            "currentPrice": 2500.0,
            "targetMeanPrice": 2750.0,
        }
    }
    monkeypatch.setattr(kite_tools, "get_settings", lambda: make_settings(tmp_path))
    monkeypatch.setattr(kite_tools, "load_yfinance_server_definition", lambda settings: object())
    monkeypatch.setattr(kite_tools, "MCPToolClient", lambda definition, timeout_seconds: FakeMCPClient(payload))

    result = await kite_tools.get_yfinance_snapshot("BSE")

    assert set(result) == {
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
    }
    assert result["fifty_two_week_low"] is None
    assert result["sector"] is None


async def test_get_yfinance_company_info_unwraps_result_json(tmp_path, monkeypatch) -> None:
    payload = {"result": json.dumps({"longName": "BSE Limited", "marketCap": 1_000_000.0})}
    monkeypatch.setattr(kite_tools, "get_settings", lambda: make_settings(tmp_path))
    monkeypatch.setattr(kite_tools, "load_yfinance_server_definition", lambda settings: object())
    monkeypatch.setattr(kite_tools, "MCPToolClient", lambda definition, timeout_seconds: FakeMCPClient(payload))

    result = await kite_tools.get_yfinance_company_info("BSE")

    assert result["longName"] == "BSE Limited"
    assert result["marketCap"] == 1_000_000.0


async def test_get_yfinance_provider_payload_collects_snapshot_and_raw_errors(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(kite_tools, "_normalize_yfinance_ticker", lambda ticker: "BSE.NS")

    async def fake_company_info(_ticker: str) -> dict:
        return {}

    async def fake_snapshot(_ticker: str) -> dict:
        return {"ticker": "BSE.NS", "cmp": 2500.0}

    monkeypatch.setattr(kite_tools, "get_yfinance_company_info", fake_company_info)
    monkeypatch.setattr(kite_tools, "get_yfinance_snapshot", fake_snapshot)

    result = await kite_tools.get_yfinance_provider_payload("BSE")

    assert result["provider"] == "yfinance"
    assert result["provider_symbol"] == "BSE.NS"
    assert result["snapshot"]["cmp"] == 2500.0
    assert result["raw"] == {}
    assert result["errors"] == ["raw company info unavailable"]
