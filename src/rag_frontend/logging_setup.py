"""Logging setup that never records the MCP bearer token."""

from __future__ import annotations

import logging


def configure_logging() -> None:
    """Send application logs to stderr and quiet HTTP client header dumps."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("mcp").setLevel(logging.WARNING)
