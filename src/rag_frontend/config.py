"""Environment configuration for the MCP client."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_MCP_SERVER_URL = "https://rag-timer-00001-production.up.railway.app/mcp/"
DEFAULT_LLM_BASE_URL = "https://api.novita.ai/openai/v1"
DEFAULT_LLM_MODEL = "google/gemma-3-27b-it"
DEFAULT_LLM_MODEL_FALLBACK = "google/gemma-3-12b-it"
LLM_KEY_ENV = "NOVITA_LLM_API_KEY_GEMMA-3-27"


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from the environment."""

    mcp_server_url: str
    mcp_api_token: str
    host: str
    port: int
    llm_base_url: str = DEFAULT_LLM_BASE_URL
    llm_model: str = DEFAULT_LLM_MODEL
    llm_model_fallback: str = DEFAULT_LLM_MODEL_FALLBACK
    llm_api_key: str = ""
    answer_use_llm: bool = True
    google_client_id: str = ""
    google_client_secret: str = ""
    google_allowed_emails: tuple[str, ...] = ()
    session_secret: str = "dev-session-change-me"
    public_base_url: str = "http://127.0.0.1:8000"
    cache_ttl_seconds: int = 600

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
    emails = tuple(
        item.strip().lower()
        for item in os.environ.get("GOOGLE_ALLOWED_EMAILS", "").split(",")
        if item.strip()
    )
    return Settings(
        mcp_server_url=url or DEFAULT_MCP_SERVER_URL,
        mcp_api_token=token,
        host=host,
        port=port,
        llm_base_url=os.environ.get("LLM_BASE_URL", DEFAULT_LLM_BASE_URL).strip()
        or DEFAULT_LLM_BASE_URL,
        llm_model=os.environ.get("LLM_MODEL", DEFAULT_LLM_MODEL).strip()
        or DEFAULT_LLM_MODEL,
        llm_model_fallback=os.environ.get(
            "LLM_MODEL_FALLBACK", DEFAULT_LLM_MODEL_FALLBACK
        ).strip()
        or DEFAULT_LLM_MODEL_FALLBACK,
        llm_api_key=os.environ.get(LLM_KEY_ENV, "").strip(),
        answer_use_llm=_flag(os.environ.get("ANSWER_USE_LLM", "true")),
        google_client_id=os.environ.get("GOOGLE_CLIENT_ID", "").strip(),
        google_client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", "").strip(),
        google_allowed_emails=emails,
        session_secret=os.environ.get("SESSION_SECRET", "").strip()
        or "dev-session-change-me",
        public_base_url=os.environ.get(
            "PUBLIC_BASE_URL", "http://127.0.0.1:8000"
        ).strip()
        or "http://127.0.0.1:8000",
        cache_ttl_seconds=int(os.environ.get("ANSWER_CACHE_TTL_SECONDS", "600")),
    )


def _flag(value: str) -> bool:
    """Interpret a common boolean environment string."""
    return value.strip().lower() in {"1", "true", "yes", "on"}
