from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str = Field(alias="ANTHROPIC_API_KEY")
    model: str = Field(default="claude-sonnet-4-6", alias="MODEL")
    max_tokens: int = Field(default=8096, alias="MAX_TOKENS")
    max_iterations: int = Field(default=10, alias="MAX_ITERATIONS")
    reports_dir: Path = Field(default=ROOT_DIR / "reports", alias="REPORTS_DIR")
    console_exports_dir: Path = Field(
        default=ROOT_DIR / "data" / "console_exports",
        alias="CONSOLE_EXPORTS_DIR",
    )
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    claude_desktop_config_path: Path = Field(
        default=Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    )
    kite_mcp_server_name: str = Field(default="kite")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv()
    settings = Settings()
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    settings.console_exports_dir.mkdir(parents=True, exist_ok=True)
    return settings


def configure_logging(level: str) -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

