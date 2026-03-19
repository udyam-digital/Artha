from __future__ import annotations

import logging
from json import JSONDecodeError, loads
from functools import lru_cache
from pathlib import Path
from typing import Any

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
    analyst_max_tokens: int = Field(default=2500, alias="ANALYST_MAX_TOKENS")
    analyst_max_searches: int = Field(default=3, alias="ANALYST_MAX_SEARCHES")
    analyst_parallelism: int = Field(default=2, alias="ANALYST_PARALLELISM")
    analyst_min_start_interval_seconds: float = Field(default=3.0, alias="ANALYST_MIN_START_INTERVAL_SECONDS")
    haiku_input_tpm: int = Field(default=50000, alias="HAIKU_INPUT_TPM")
    haiku_output_tpm: int = Field(default=10000, alias="HAIKU_OUTPUT_TPM")
    summary_max_tokens: int = Field(default=700, alias="SUMMARY_MAX_TOKENS")
    company_analysis_max_age_days: int = Field(default=7, alias="COMPANY_ANALYSIS_MAX_AGE_DAYS")
    max_tokens: int = Field(default=8096, alias="MAX_TOKENS")
    max_iterations: int = Field(default=10, alias="MAX_ITERATIONS")
    transient_retry_attempts: int = Field(default=3, alias="TRANSIENT_RETRY_ATTEMPTS")
    transient_retry_base_delay_seconds: float = Field(default=1.0, alias="TRANSIENT_RETRY_BASE_DELAY_SECONDS")
    reports_dir: Path = Field(default=ROOT_DIR / "reports", alias="REPORTS_DIR")
    llm_usage_dir: Path = Field(default=ROOT_DIR / "reports" / "usage", alias="LLM_USAGE_DIR")
    telemetry_service_name: str = Field(default="artha", alias="TELEMETRY_SERVICE_NAME")
    telemetry_environment: str = Field(default="development", alias="TELEMETRY_ENVIRONMENT")
    telemetry_enabled: bool = Field(default=True, alias="TELEMETRY_ENABLED")
    otel_exporter_otlp_endpoint: str = Field(default="", alias="OTEL_EXPORTER_OTLP_ENDPOINT")
    otel_exporter_otlp_headers: dict[str, str] = Field(default_factory=dict, alias="OTEL_EXPORTER_OTLP_HEADERS")
    langfuse_public_key: str = Field(default="", alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str = Field(default="", alias="LANGFUSE_SECRET_KEY")
    langfuse_base_url: str = Field(default="https://cloud.langfuse.com", alias="LANGFUSE_BASE_URL")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    tavily_api_key: str = Field(default="", alias="TAVILY_API_KEY")
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

    @field_validator(
        "otel_exporter_otlp_endpoint",
        "langfuse_public_key",
        "langfuse_secret_key",
        "tavily_api_key",
        mode="before",
    )
    @classmethod
    def parse_stripped_strings(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("langfuse_base_url", mode="before")
    @classmethod
    def parse_langfuse_base_url(cls, value: object) -> str:
        if value is None:
            return "https://cloud.langfuse.com"
        url = str(value).strip()
        return url or "https://cloud.langfuse.com"

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

    @field_validator("otel_exporter_otlp_headers", mode="before")
    @classmethod
    def parse_otel_exporter_otlp_headers(cls, value: object) -> dict[str, str]:
        if value in (None, "", {}):
            return {}
        if isinstance(value, dict):
            return {str(key): str(val) for key, val in value.items()}
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return {}
            try:
                parsed: Any = loads(raw)
            except JSONDecodeError:
                return cls._parse_otel_header_pairs(raw)
            if isinstance(parsed, dict):
                return {str(key): str(val) for key, val in parsed.items()}
            return cls._parse_otel_header_pairs(raw)
        raise ValueError("OTEL_EXPORTER_OTLP_HEADERS must be a dict, JSON object string, or KEY=VALUE pairs.")

    @classmethod
    def _parse_otel_header_pairs(cls, raw: str) -> dict[str, str]:
        headers: dict[str, str] = {}
        for part in raw.split(","):
            item = part.strip()
            if not item:
                continue
            key, sep, val = item.partition("=")
            if not sep or not key.strip() or not val.strip():
                raise ValueError(
                    "OTEL_EXPORTER_OTLP_HEADERS must be a JSON object string or comma-separated KEY=VALUE pairs."
                )
            headers[key.strip()] = val.strip()
        if not headers:
            raise ValueError(
                "OTEL_EXPORTER_OTLP_HEADERS must be a JSON object string or comma-separated KEY=VALUE pairs."
            )
        return headers


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv()
    settings = Settings()
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    settings.llm_usage_dir.mkdir(parents=True, exist_ok=True)
    settings.kite_data_dir.mkdir(parents=True, exist_ok=True)
    return settings


def configure_logging(level: str) -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
