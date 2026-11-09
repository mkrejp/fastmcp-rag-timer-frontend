"""Environment configuration for the MCP client."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_MCP_SERVER_URL = "https://rag-timer-00001-production.up.railway.app/mcp/"


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from the environment."""

    mcp_server_url: str
    mcp_api_token: str
    host: str
    port: int

    @property
    def token_configured(self) -> bool:
        """Return whether a bearer token is available for the MCP server."""
        return bool(self.mcp_api_token)


def load_settings() -> Settings:
    """Load settings from process environment variables.

    ``MCP_API_TOKEN`` may be empty. Query routes then refuse to call the
    MCP server. The HTML page still loads.
    """
    token = os.environ.get("MCP_API_TOKEN", "").strip()
    url = os.environ.get("MCP_SERVER_URL", DEFAULT_MCP_SERVER_URL).strip()
    host = os.environ.get("HOST", "0.0.0.0").strip() or "0.0.0.0"
    port = int(os.environ.get("PORT", "8000"))
    return Settings(
        mcp_server_url=url or DEFAULT_MCP_SERVER_URL,
        mcp_api_token=token,
        host=host,
        port=port,
    )
