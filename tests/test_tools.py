from pathlib import Path

from config import Settings
from tools import (
    MCPServerDefinition,
    _holding_market_value,
    extract_auth_url,
    load_kite_server_definition,
    profile_requires_login,
    save_kite_artifact,
)


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
