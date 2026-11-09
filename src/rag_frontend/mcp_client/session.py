"""Streamable HTTP MCP client for read-only RAG tools, including images."""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, ImageContent, TextContent

from rag_frontend.config import Settings

logger = logging.getLogger("rag_frontend.mcp")


class MissingTokenError(Exception):
    """Raised when a query is attempted without ``MCP_API_TOKEN``."""


class McpCallError(Exception):
    """Raised when the MCP transport fails before a tool result arrives."""


@dataclass(frozen=True)
class ImageBytes:
    """Inline image bytes, or a text explanation when bytes are unavailable."""

    data: bytes | None
    mime_type: str | None
    message: str | None


class McpToolClient(Protocol):
    """Client the HTTP routes use to call MCP tools."""

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Call ``name`` and return the tool's text."""

    async def fetch_image(self, image_id: str) -> ImageBytes:
        """Load one stored image by ``image_id``."""


class HttpMcpClient:
    """Open one stateless Streamable HTTP session per tool call."""

    def __init__(self, settings: Settings) -> None:
        """Store the server URL and bearer token."""
        self._settings = settings

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Call ``name`` and return its text content."""
        result = await self._invoke(name, arguments)
        return tool_result_text(result)

    async def fetch_image(self, image_id: str) -> ImageBytes:
        """Call ``get_image`` and decode inline bytes when the server sends them."""
        result = await self._invoke("get_image", {"image_id": image_id})
        return image_from_result(result)

    async def _invoke(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        """Call ``name`` on the configured MCP server.

        The bearer token is sent only on this HTTP client. It is not logged.
        """
        token = self._settings.mcp_api_token
        if not token:
            raise MissingTokenError("MCP_API_TOKEN is not set.")
        logger.info("MCP tool %s argument_count=%s", name, len(arguments))
        timeout = httpx.Timeout(30.0, read=120.0)
        headers = {"Authorization": f"Bearer {token}"}
        try:
            async with httpx.AsyncClient(
                headers=headers, timeout=timeout
            ) as http_client:
                async with streamable_http_client(
                    self._settings.mcp_server_url,
                    http_client=http_client,
                    terminate_on_close=False,
                ) as (
                    read_stream,
                    write_stream,
                    _get_session_id,
                ):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        return await session.call_tool(name, arguments)
        except MissingTokenError:
            raise
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            logger.warning("MCP HTTP status %s for tool %s", status, name)
            raise McpCallError(f"MCP server returned HTTP {status}.") from exc
        except Exception as exc:
            logger.warning("MCP call %s failed: %s", name, type(exc).__name__)
            raise McpCallError("MCP call failed.") from exc


def tool_result_text(result: CallToolResult) -> str:
    """Join text blocks from a tool result.

    A protocol-level tool error is prefixed so the parsers treat it like the
    server's ``Error …`` strings.
    """
    parts: list[str] = []
    for block in result.content:
        if isinstance(block, TextContent):
            parts.append(block.text)
    text = "\n".join(parts).strip()
    if result.isError and not text.startswith("Error"):
        detail = text or "tool failed"
        return f"Error {detail}"
    return text


def image_from_result(result: CallToolResult) -> ImageBytes:
    """Take the first inline image, or the tool's text when bytes are absent."""
    for block in result.content:
        if isinstance(block, ImageContent):
            return ImageBytes(
                data=base64.b64decode(block.data),
                mime_type=block.mimeType,
                message=None,
            )
    text = tool_result_text(result) or "Image not found."
    return ImageBytes(data=None, mime_type=None, message=text)
