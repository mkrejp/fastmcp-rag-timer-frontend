"""Run the query page."""

from __future__ import annotations

import uvicorn

from rag_frontend.api.routes import create_app
from rag_frontend.config import load_settings
from rag_frontend.logging_setup import configure_logging


def main() -> None:
    """Start the HTTP server that hosts the page and the MCP client."""
    configure_logging()
    settings = load_settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
