"""NSE MCP provider — server definition for the NSE MCP server."""

from __future__ import annotations

from config import DEFAULT_NSE_MCP_ARGS, DEFAULT_NSE_MCP_COMMAND, Settings, get_settings
from providers.mcp_client import MCPServerDefinition


def load_nse_server_definition(settings: Settings | None = None) -> MCPServerDefinition:
    settings = settings or get_settings()
    command = settings.nse_mcp_command.strip() or DEFAULT_NSE_MCP_COMMAND
    args = settings.nse_mcp_args or list(DEFAULT_NSE_MCP_ARGS)
    return MCPServerDefinition(
        transport="stdio",
        url=None,
        command=command,
        args=args,
        env=settings.nse_mcp_env_json,
    )
