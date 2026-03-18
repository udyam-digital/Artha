from __future__ import annotations

import logging
from json import JSONDecodeError, loads
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_KITE_MCP_URL = "https://mcp.kite.trade/mcp"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str = Field(alias="ANTHROPIC_API_KEY")
    model: str = Field(default="claude-sonnet-4-6", alias="MODEL")
    analyst_model: str = Field(default="claude-haiku-4-5", alias="ANALYST_MODEL")
    max_tokens: int = Field(default=8096, alias="MAX_TOKENS")
    max_iterations: int = Field(default=10, alias="MAX_ITERATIONS")
    reports_dir: Path = Field(default=ROOT_DIR / "reports", alias="REPORTS_DIR")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    kite_mcp_url: str = Field(default=DEFAULT_KITE_MCP_URL, alias="KITE_MCP_URL")
    kite_mcp_command: str = Field(default="", alias="KITE_MCP_COMMAND")
    kite_mcp_args: list[str] = Field(default_factory=list, alias="KITE_MCP_ARGS")
    kite_mcp_env_json: dict[str, str] = Field(default_factory=dict, alias="KITE_MCP_ENV_JSON")
    kite_mcp_timeout_seconds: int = Field(default=30, alias="KITE_MCP_TIMEOUT_SECONDS")
    kite_data_dir: Path = Field(default=ROOT_DIR / "data" / "kite", alias="KITE_DATA_DIR")
    kite_login_timeout_seconds: int = Field(default=180, alias="KITE_LOGIN_TIMEOUT_SECONDS")
    kite_login_poll_interval_seconds: int = Field(default=3, alias="KITE_LOGIN_POLL_INTERVAL_SECONDS")

    @field_validator("kite_mcp_url", mode="before")
    @classmethod
    def parse_kite_mcp_url(cls, value: object) -> str:
        if value is None:
            return DEFAULT_KITE_MCP_URL
        url = str(value).strip()
        return url or DEFAULT_KITE_MCP_URL

    @field_validator("kite_mcp_command", mode="before")
    @classmethod
    def parse_kite_mcp_command(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("kite_mcp_args", mode="before")
    @classmethod
    def parse_kite_mcp_args(cls, value: object) -> list[str]:
        if value in (None, "", []):
            return []
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            try:
                parsed = loads(value)
            except JSONDecodeError as exc:
                raise ValueError("KITE_MCP_ARGS must be a JSON array string.") from exc
            if not isinstance(parsed, list):
                raise ValueError("KITE_MCP_ARGS must decode to a list.")
            return [str(item) for item in parsed]
        raise ValueError("KITE_MCP_ARGS must be a list or JSON array string.")

    @field_validator("kite_mcp_env_json", mode="before")
    @classmethod
    def parse_kite_mcp_env_json(cls, value: object) -> dict[str, str]:
        if value in (None, "", {}):
            return {}
        if isinstance(value, dict):
            return {str(key): str(val) for key, val in value.items()}
        if isinstance(value, str):
            try:
                parsed = loads(value)
            except JSONDecodeError as exc:
                raise ValueError("KITE_MCP_ENV_JSON must be a JSON object string.") from exc
            if not isinstance(parsed, dict):
                raise ValueError("KITE_MCP_ENV_JSON must decode to an object.")
            return {str(key): str(val) for key, val in parsed.items()}
        raise ValueError("KITE_MCP_ENV_JSON must be a dict or JSON object string.")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv()
    settings = Settings()
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    settings.kite_data_dir.mkdir(parents=True, exist_ok=True)
    return settings


def configure_logging(level: str) -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
