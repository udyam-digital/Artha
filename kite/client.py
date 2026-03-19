from __future__ import annotations

import json
import logging
import os
from asyncio import wait_for
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from config import Settings, get_settings


logger = logging.getLogger(__name__)


class ToolExecutionError(RuntimeError):
    pass


@dataclass
class MCPServerDefinition:
    transport: str
    url: str | None
    command: str
    args: list[str]
    env: dict[str, str]


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
        raise ToolExecutionError(
            "Kite MCP is not configured for Artha. Set KITE_MCP_URL or KITE_MCP_COMMAND in .env."
        )

    return MCPServerDefinition(
        transport="stdio",
        url=None,
        command=settings.kite_mcp_command,
        args=settings.kite_mcp_args,
        env=settings.kite_mcp_env_json,
    )


class KiteMCPClient:
    def __init__(self, definition: MCPServerDefinition, timeout_seconds: int = 30):
        self.definition = definition
        self.timeout_seconds = timeout_seconds
        self._stack = AsyncExitStack()
        self._session = None

    async def __aenter__(self) -> "KiteMCPClient":
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:
            raise ToolExecutionError(
                "The 'mcp' package is required for Kite MCP access. Install requirements first."
            ) from exc

        try:
            if self.definition.transport == "http":
                read, write, _ = await self._stack.enter_async_context(
                    streamable_http_client(self.definition.url or "")
                )
            else:
                env = dict(os.environ)
                env.update(self.definition.env)
                server_params = StdioServerParameters(
                    command=self.definition.command,
                    args=self.definition.args,
                    env=env,
                )
                read, write = await wait_for(
                    self._stack.enter_async_context(stdio_client(server_params)),
                    timeout=self.timeout_seconds,
                )
            self._session = await wait_for(
                self._stack.enter_async_context(ClientSession(read, write)),
                timeout=self.timeout_seconds,
            )
            await wait_for(self._session.initialize(), timeout=self.timeout_seconds)
        except Exception as exc:
            try:
                await self._stack.aclose()
            except Exception:
                logger.debug("Ignoring MCP cleanup error after initialization failure", exc_info=True)
            raise ToolExecutionError(
                "Failed to initialize Kite MCP for Artha. Verify KITE_MCP_URL or KITE_MCP_COMMAND, "
                "network access, and your Zerodha session."
            ) from exc
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            await self._stack.aclose()
        except Exception as close_exc:
            if self.definition.transport == "http":
                logger.debug("Ignoring streamable HTTP shutdown bug during Kite MCP close", exc_info=True)
                return
            raise

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        if self._session is None:
            raise ToolExecutionError("Kite MCP client is not connected.")

        result = await self._session.call_tool(name, arguments or {})
        if getattr(result, "structuredContent", None) is not None:
            return result.structuredContent

        content = []
        for block in getattr(result, "content", []):
            text = getattr(block, "text", None)
            if text is not None:
                content.append(text)
        if not content:
            return {}

        joined = "\n".join(content).strip()
        try:
            return json.loads(joined)
        except json.JSONDecodeError:
            return {"raw_text": joined}
