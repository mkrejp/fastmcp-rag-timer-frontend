"""Settings loaded from the environment."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rag_frontend.config import load_settings
from rag_frontend.redact import install_log_redaction

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


def test_log_records_do_not_keep_the_runtime_token() -> None:
    """Log lines created after startup do not contain the bearer token."""
    secret = "token-do-not-leak-9f3a"
    install_log_redaction(secret)
    logger = logging.getLogger("rag_frontend.test")
    record = logger.makeRecord(
        logger.name,
        logging.WARNING,
        __file__,
        0,
        "Authorization: Bearer %s",
        (secret,),
        None,
    )
    assert secret not in record.getMessage()
    assert "[redacted]" in record.getMessage()
