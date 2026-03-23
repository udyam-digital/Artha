"""Generic MCP client — transport-agnostic base used by all MCP providers (Kite, MoSPI, yfinance, NSE)."""

from __future__ import annotations

import json
import logging
import os
from asyncio import wait_for
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

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


class MCPToolClient:
    def __init__(self, definition: MCPServerDefinition, timeout_seconds: int = 30):
        self.definition = definition
        self.timeout_seconds = timeout_seconds
        self._stack = AsyncExitStack()
        self._session = None

    async def __aenter__(self) -> MCPToolClient:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:
            raise ToolExecutionError(
                "The 'mcp' package is required for MCP access. Install requirements first."
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
                f"Failed to initialize MCP server at {self.definition.url or self.definition.command!r}."
            ) from exc
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            await self._stack.aclose()
        except Exception as close_exc:
            if self.definition.transport == "http":
                logger.debug("Ignoring streamable HTTP shutdown bug during MCP close", exc_info=True)
                return
            if "Attempted to exit cancel scope in a different task" in str(close_exc):
                logger.debug("Ignoring stdio MCP shutdown cancel-scope bug during close", exc_info=True)
                return
            raise

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        if self._session is None:
            raise ToolExecutionError("MCP client is not connected.")

        try:
            result = await wait_for(
                self._session.call_tool(name, arguments or {}),
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            raise ToolExecutionError(f"MCP tool '{name}' failed or timed out.") from exc

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
