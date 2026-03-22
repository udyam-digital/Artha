from __future__ import annotations

import pytest

import kite.tools as kite_tools
from config import Settings

pytestmark = pytest.mark.anyio


class FakeNSEClient:
    def __init__(self, payloads: dict[str, object]) -> None:
        self.payloads = payloads

    async def __aenter__(self) -> FakeNSEClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def call_tool(self, name: str, arguments=None):
        del arguments
        payload = self.payloads[name]
        if isinstance(payload, Exception):
            raise payload
        return payload


def make_settings(tmp_path) -> Settings:
    return Settings(
        ANTHROPIC_API_KEY="test-key",  # pragma: allowlist secret
        REPORTS_DIR=str(tmp_path / "reports"),
        KITE_DATA_DIR=str(tmp_path / "kite"),
    )


async def test_get_nse_india_provider_payload_builds_snapshot(tmp_path, monkeypatch) -> None:
    payloads = {
        "get_equity_details": {
            "result": {
                "info": {
                    "companyName": "BSE Limited",
                    "industry": "Capital Markets",
                    "isFNOSec": False,
                },
                "priceInfo": {
                    "lastPrice": "2500.5",
                    "previousClose": "2490.0",
                    "intraDayHighLow": {"max": "2510.0", "min": "2485.0"},
                    "weekHighLow": {"max": "3000.0", "min": "1800.0"},
                },
                "metadata": {
                    "marketCap": "1000000.0",
                    "listingDate": "2017-02-03",
                    "activeSeries": ["EQ"],
                },
                "securityInfo": {
                    "boardStatus": "Main",
                    "delivToTradedQty": "51.2",
                },
            }
        },
        "get_equity_trade_info": {
            "result": {
                "marketDeptOrderBook": {
                    "totalTradedVolume": "1200",
                    "totalTradedValue": "5500000",
                }
            }
        },
        "get_equity_corporate_info": {"result": {"events": []}},
    }
    monkeypatch.setattr(kite_tools, "get_settings", lambda: make_settings(tmp_path))
    monkeypatch.setattr(kite_tools, "load_nse_server_definition", lambda settings: object())
    monkeypatch.setattr(kite_tools, "MCPToolClient", lambda definition, timeout_seconds: FakeNSEClient(payloads))

    result = await kite_tools.get_nse_india_provider_payload("BSE")

    assert result["provider"] == "nse_india"
    assert result["provider_symbol"] == "BSE"
    assert result["snapshot"]["company_name"] == "BSE Limited"
    assert result["snapshot"]["last_price"] == 2500.5
    assert result["snapshot"]["fifty_two_week_high"] == 3000.0
    assert result["snapshot"]["total_traded_volume"] == 1200.0
    assert result["errors"] == []


async def test_get_nse_india_provider_payload_collects_partial_errors(tmp_path, monkeypatch) -> None:
    payloads = {
        "get_equity_details": {"result": {"info": {"symbol": "BSE"}}},
        "get_equity_trade_info": RuntimeError("trade info unavailable"),
        "get_equity_corporate_info": {"result": {"events": []}},
    }
    monkeypatch.setattr(kite_tools, "get_settings", lambda: make_settings(tmp_path))
    monkeypatch.setattr(kite_tools, "load_nse_server_definition", lambda settings: object())
    monkeypatch.setattr(kite_tools, "MCPToolClient", lambda definition, timeout_seconds: FakeNSEClient(payloads))

    result = await kite_tools.get_nse_india_provider_payload("BSE")

    assert result["snapshot"]["company_name"] == "BSE"
    assert result["raw"]["trade_info"] == {}
    assert result["errors"] == ["trade_info: trade info unavailable"]
