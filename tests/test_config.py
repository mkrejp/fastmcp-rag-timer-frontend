"""Settings loaded from the environment."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rag_frontend.config import load_settings

if TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch


def test_load_settings_listens_on_all_interfaces(monkeypatch: MonkeyPatch) -> None:
    """The default bind address is reachable from Railway's proxy."""
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.delenv("MCP_API_TOKEN", raising=False)
    monkeypatch.delenv("MCP_SERVER_URL", raising=False)
    settings = load_settings()
    assert settings.host == "0.0.0.0"
    assert settings.port == 8000
    assert settings.mcp_api_token == ""
