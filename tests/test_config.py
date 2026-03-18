import pytest

from config import Settings


def test_kite_mcp_args_parse_from_json() -> None:
    settings = Settings(
        ANTHROPIC_API_KEY="test-key",
        KITE_MCP_URL="",
        KITE_MCP_COMMAND="npx",
        KITE_MCP_ARGS='["mcp-remote","https://mcp.kite.trade/mcp"]',
    )
    assert settings.kite_mcp_args == ["mcp-remote", "https://mcp.kite.trade/mcp"]


def test_kite_mcp_env_json_parse_from_json() -> None:
    settings = Settings(
        ANTHROPIC_API_KEY="test-key",
        KITE_MCP_URL="",
        KITE_MCP_COMMAND="npx",
        KITE_MCP_ENV_JSON='{"FOO":"bar","BAZ":"1"}',
    )
    assert settings.kite_mcp_env_json == {"FOO": "bar", "BAZ": "1"}


def test_invalid_kite_mcp_args_raise() -> None:
    with pytest.raises(ValueError):
        Settings(
            ANTHROPIC_API_KEY="test-key",
            KITE_MCP_URL="",
            KITE_MCP_COMMAND="npx",
            KITE_MCP_ARGS='{"not":"a-list"}',
        )


def test_kite_data_dir_is_parsed() -> None:
    settings = Settings(
        ANTHROPIC_API_KEY="test-key",
        KITE_DATA_DIR="./data/kite",
    )
    assert settings.kite_data_dir.name == "kite"


def test_kite_mcp_url_defaults_to_hosted_endpoint() -> None:
    settings = Settings(ANTHROPIC_API_KEY="test-key")
    assert settings.kite_mcp_url == "https://mcp.kite.trade/mcp"
