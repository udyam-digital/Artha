import asyncio
from types import ModuleType
from pathlib import Path

from config import Settings
from kite.client import KiteMCPClient, MCPServerDefinition, ToolExecutionError, load_kite_server_definition
from kite.tools import _holding_market_value, extract_auth_url, kite_get_price_history, profile_requires_login, save_kite_artifact
from search.tavily import DEFAULT_TAVILY_MAX_RESULTS, get_tavily_search_tool_definition, tavily_search


def test_load_kite_server_definition_from_env() -> None:
    settings = Settings(ANTHROPIC_API_KEY="test-key")
    settings.kite_mcp_url = ""
    settings.kite_mcp_command = "npx"
    settings.kite_mcp_args = ["mcp-remote", "https://mcp.kite.trade/mcp"]
    settings.kite_mcp_env_json = {"NODE_ENV": "production"}
    definition = load_kite_server_definition(settings)
    assert definition == MCPServerDefinition(
        transport="stdio",
        url=None,
        command="npx",
        args=["mcp-remote", "https://mcp.kite.trade/mcp"],
        env={"NODE_ENV": "production"},
    )


def test_missing_kite_mcp_config_uses_hosted_default() -> None:
    settings = Settings(ANTHROPIC_API_KEY="test-key")
    assert load_kite_server_definition(settings) == MCPServerDefinition(
        transport="http",
        url="https://mcp.kite.trade/mcp",
        command="",
        args=[],
        env={},
    )


def test_extract_auth_url_finds_nested_url() -> None:
    payload = {"status": "ok", "data": {"login_url": "https://kite.trade/connect/login?foo=bar"}}
    assert extract_auth_url(payload) == "https://kite.trade/connect/login?foo=bar"


def test_save_kite_artifact_writes_latest_copy(tmp_path: Path) -> None:
    settings = Settings(
        ANTHROPIC_API_KEY="test-key",
        KITE_DATA_DIR=str(tmp_path),
    )
    artifact = save_kite_artifact(
        {"status": "ok"},
        settings=settings,
        category="auth",
        stem="login",
    )
    latest = tmp_path / "auth" / "latest_login.json"
    assert artifact.exists()
    assert latest.exists()


def test_profile_requires_login_detects_hosted_message() -> None:
    assert profile_requires_login({"raw_text": "Please log in first using the login tool"}) is True
    assert profile_requires_login({"user_name": "Saksham"}) is False
    assert profile_requires_login({}) is True
    assert profile_requires_login({"status": "ok"}) is True


def test_holding_market_value_falls_back_to_quantity_times_last_price() -> None:
    assert _holding_market_value({"quantity": 10, "last_price": 123.4}) == 1234.0
    assert _holding_market_value({"current_value": 999.0, "quantity": 10, "last_price": 123.4}) == 999.0


class FakeKiteClient:
    def __init__(self, payload):
        self.payload = payload

    async def call_tool(self, name, payload=None):
        return self.payload


def test_kite_mcp_client_call_tool_applies_timeout() -> None:
    class HangingSession:
        async def call_tool(self, name, arguments):
            del name, arguments
            await asyncio.sleep(0.05)

    client = KiteMCPClient(MCPServerDefinition("http", "https://example.com", "", [], {}), timeout_seconds=0)
    client._session = HangingSession()

    try:
        asyncio.run(client.call_tool("get_profile"))
    except ToolExecutionError as exc:
        assert "get_profile" in str(exc)
    else:
        raise AssertionError("Expected ToolExecutionError")


def test_kite_get_price_history_returns_summary_only() -> None:
    result = asyncio.run(
        kite_get_price_history(
            FakeKiteClient(
                {
                    "candles": [
                        ["2025-03-01", 90, 100, 80, 95, 1000],
                        ["2026-03-01", 100, 120, 85, 110, 1000],
                    ]
                }
            ),
            tradingsymbol="KPITTECH",
            instrument_token=123,
        )
    )
    assert set(result) == {
        "52w_high",
        "52w_low",
        "current_vs_52w_high_pct",
        "price_1y_ago",
        "price_change_1y_pct",
    }
    assert result["52w_high"] == 120.0
    assert result["52w_low"] == 80.0
    assert result["price_1y_ago"] == 95.0
    assert result["price_change_1y_pct"] > 0


def test_kite_get_price_history_raises_on_empty_history() -> None:
    try:
        asyncio.run(
            kite_get_price_history(
                FakeKiteClient({"candles": []}),
                tradingsymbol="KPITTECH",
                instrument_token=123,
            )
        )
    except ToolExecutionError as exc:
        assert "No historical data available" in str(exc)
    else:
        raise AssertionError("Expected ToolExecutionError for empty historical data")


def test_get_tavily_search_tool_definition_uses_configured_budget() -> None:
    settings = Settings(ANTHROPIC_API_KEY="test-key", ANALYST_MAX_SEARCHES=4)
    tool = get_tavily_search_tool_definition(settings)
    assert tool["name"] == "tavily_search"
    assert "4 searches" in tool["description"]
    assert tool["input_schema"]["properties"]["max_results"]["default"] == DEFAULT_TAVILY_MAX_RESULTS


def test_tavily_search_formats_summary_and_results(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, api_key):
            self.api_key = api_key

        def search(self, **kwargs):
            assert kwargs["search_depth"] == "basic"
            assert kwargs["max_results"] == 2
            return {
                "answer": "Short answer",
                "results": [
                    {
                        "title": "Result 1",
                        "content": "A" * 450,
                        "url": "https://example.com/1",
                    },
                    {
                        "title": "Result 2",
                        "content": "B" * 50,
                        "url": "https://example.com/2",
                    },
                ],
            }

    fake_module = ModuleType("tavily")
    fake_module.TavilyClient = FakeClient
    monkeypatch.setitem(__import__("sys").modules, "tavily", fake_module)
    settings = Settings(ANTHROPIC_API_KEY="test-key", TAVILY_API_KEY="tvly-test")
    result = tavily_search("KPIT results", max_results=2, settings=settings)
    assert "Summary: Short answer" in result
    assert "[Result 1]" in result
    assert "https://example.com/1" in result
    assert "..." in result
