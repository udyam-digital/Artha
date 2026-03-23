"""yfinance MCP provider — server definition for the yfinance MCP server."""

from __future__ import annotations

from config import DEFAULT_YFINANCE_MCP_ARGS, DEFAULT_YFINANCE_MCP_COMMAND, Settings, get_settings
from providers.mcp_client import MCPServerDefinition


def load_yfinance_server_definition(settings: Settings | None = None) -> MCPServerDefinition:
    settings = settings or get_settings()
    command = settings.yfinance_mcp_command.strip() or DEFAULT_YFINANCE_MCP_COMMAND
    args = settings.yfinance_mcp_args or list(DEFAULT_YFINANCE_MCP_ARGS)
    return MCPServerDefinition(
        transport="stdio",
        url=None,
        command=command,
        args=args,
        env=settings.yfinance_mcp_env_json,
    )
