"""Kite MCP provider — server definition and client for Zerodha/Kite MCP."""

from __future__ import annotations

from config import Settings, get_settings
from providers.mcp_client import MCPServerDefinition, MCPToolClient, ToolExecutionError


def load_kite_server_definition(settings: Settings | None = None) -> MCPServerDefinition:
    settings = settings or get_settings()
    if settings.kite_mcp_url.strip():
        return MCPServerDefinition(
            transport="http",
            url=settings.kite_mcp_url,
            command="",
            args=[],
            env={},
        )

    if not settings.kite_mcp_command.strip():
        raise ToolExecutionError("Kite MCP is not configured for Artha. Set KITE_MCP_URL or KITE_MCP_COMMAND in .env.")

    return MCPServerDefinition(
        transport="stdio",
        url=None,
        command=settings.kite_mcp_command,
        args=settings.kite_mcp_args,
        env=settings.kite_mcp_env_json,
    )


class KiteMCPClient(MCPToolClient):
    pass
