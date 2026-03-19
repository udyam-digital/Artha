from pathlib import Path

import pytest

import config
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


def test_otel_headers_parse_from_json() -> None:
    settings = Settings(
        ANTHROPIC_API_KEY="test-key",
        OTEL_EXPORTER_OTLP_HEADERS='{"Authorization":"Basic abc","x-test":"1"}',
    )
    assert settings.otel_exporter_otlp_headers == {"Authorization": "Basic abc", "x-test": "1"}


def test_invalid_otel_headers_raise() -> None:
    with pytest.raises(ValueError):
        Settings(
            ANTHROPIC_API_KEY="test-key",
            OTEL_EXPORTER_OTLP_HEADERS='["bad"]',
        )


def test_langfuse_base_url_is_stripped() -> None:
    settings = Settings(
        ANTHROPIC_API_KEY="test-key",
        LANGFUSE_BASE_URL=" https://cloud.langfuse.com ",
    )
    assert settings.langfuse_base_url == "https://cloud.langfuse.com"


def test_analyst_runtime_controls_parse() -> None:
    settings = Settings(
        ANTHROPIC_API_KEY="test-key",
        TAVILY_API_KEY=" tvly-test ",
        ANALYST_MAX_SEARCHES=3,
        ANALYST_PARALLELISM=2,
        ANALYST_MIN_START_INTERVAL_SECONDS=12.5,
    )
    assert settings.tavily_api_key == "tvly-test"
    assert settings.analyst_max_searches == 3
    assert settings.analyst_parallelism == 2
    assert settings.analyst_min_start_interval_seconds == 12.5


def test_none_inputs_are_normalized() -> None:
    settings = Settings(
        ANTHROPIC_API_KEY="test-key",
        KITE_MCP_URL=None,
        KITE_MCP_COMMAND=None,
        LANGFUSE_PUBLIC_KEY=None,
    )
    assert settings.kite_mcp_url == "https://mcp.kite.trade/mcp"
    assert settings.kite_mcp_command == ""
    assert settings.langfuse_public_key == ""


def test_list_and_dict_inputs_are_preserved() -> None:
    settings = Settings(
        ANTHROPIC_API_KEY="test-key",
        KITE_MCP_ARGS=["a", 1],
        KITE_MCP_ENV_JSON={"FOO": 1},
        OTEL_EXPORTER_OTLP_HEADERS={"Authorization": "Basic abc"},
    )
    assert settings.kite_mcp_args == ["a", "1"]
    assert settings.kite_mcp_env_json == {"FOO": "1"}
    assert settings.otel_exporter_otlp_headers == {"Authorization": "Basic abc"}


def test_invalid_non_string_inputs_raise() -> None:
    with pytest.raises(ValueError):
        Settings(ANTHROPIC_API_KEY="test-key", KITE_MCP_ARGS=123)
    with pytest.raises(ValueError):
        Settings(ANTHROPIC_API_KEY="test-key", KITE_MCP_ENV_JSON=123)
    with pytest.raises(ValueError):
        Settings(ANTHROPIC_API_KEY="test-key", OTEL_EXPORTER_OTLP_HEADERS=123)


def test_invalid_json_inputs_raise() -> None:
    with pytest.raises(ValueError):
        Settings(ANTHROPIC_API_KEY="test-key", KITE_MCP_ARGS="not-json")
    with pytest.raises(ValueError):
        Settings(ANTHROPIC_API_KEY="test-key", KITE_MCP_ENV_JSON="not-json")
    with pytest.raises(ValueError):
        Settings(ANTHROPIC_API_KEY="test-key", OTEL_EXPORTER_OTLP_HEADERS="not-json")


def test_invalid_json_shape_inputs_raise() -> None:
    with pytest.raises(ValueError):
        Settings(ANTHROPIC_API_KEY="test-key", KITE_MCP_ENV_JSON='["bad"]')


def test_get_settings_creates_directories_and_configure_logging(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("LLM_USAGE_DIR", str(tmp_path / "reports" / "usage"))
    monkeypatch.setenv("KITE_DATA_DIR", str(tmp_path / "kite"))
    config.get_settings.cache_clear()
    settings = config.get_settings()
    assert settings.reports_dir.exists()
    assert settings.llm_usage_dir.exists()
    assert settings.kite_data_dir.exists()
    config.configure_logging("debug")
    config.get_settings.cache_clear()
