"""Tests for MCP result decoding. The network session is not opened."""

from __future__ import annotations

import base64

from mcp.types import CallToolResult, ImageContent, TextContent

from rag_frontend.mcp_client.session import image_from_result, tool_result_text


def test_tool_result_text_marks_protocol_errors() -> None:
    """A protocol error becomes the same ``Error`` prefix the parsers expect."""
    result = CallToolResult(
        content=[TextContent(type="text", text="boom")],
        isError=True,
    )
    assert tool_result_text(result) == "Error boom"


def test_image_from_result_decodes_inline_bytes() -> None:
    """Inline MCP image content is decoded and not turned into text."""
    encoded = base64.b64encode(b"png-bytes").decode("ascii")
    result = CallToolResult(
        content=[ImageContent(type="image", data=encoded, mimeType="image/png")],
        isError=False,
    )
    payload = image_from_result(result)
    assert payload.data == b"png-bytes"
    assert payload.mime_type == "image/png"
    assert payload.message is None
