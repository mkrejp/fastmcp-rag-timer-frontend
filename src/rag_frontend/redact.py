"""Strip the MCP bearer token from anything a browser or log can see."""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_TEXT_TYPES = ("text/", "application/json", "application/problem+json")
_REDACTED = b"[redacted]"


def redact_text(value: str, secret: str) -> str:
    """Replace ``secret`` in ``value``. An empty secret leaves the text unchanged."""
    if not secret or secret not in value:
        return value
    return value.replace(secret, "[redacted]")


def install_log_redaction(secret: str) -> None:
    """Redact the bearer token from every log record as it is created."""
    if not secret:
        return
    previous = logging.getLogRecordFactory()
    if getattr(previous, "_redacts", None) == secret:
        return

    def factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = previous(*args, **kwargs)  # type: ignore[arg-type]
        try:
            rendered = record.getMessage()
        except (TypeError, ValueError):
            return record
        if secret in rendered:
            record.msg = rendered.replace(secret, "[redacted]")
            record.args = ()
            record.message = record.msg
        return record

    factory._redacts = secret  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)


class RedactTokenMiddleware(BaseHTTPMiddleware):
    """Remove the bearer token from text responses before they leave the process."""

    def __init__(self, app: object, token: str) -> None:
        """Store the runtime token. Binary responses are left unchanged."""
        super().__init__(app)  # type: ignore[arg-type]
        self._token = token.encode("utf-8")

    async def dispatch(self, request: Request, call_next: object) -> Response:
        """Return the downstream response, with the token stripped from text bodies."""
        response = await call_next(request)  # type: ignore[operator]
        if not self._token:
            return response
        media_type = response.media_type or ""
        content_type = response.headers.get("content-type", media_type)
        if not _is_text(content_type):
            return response
        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        if self._token in body:
            body = body.replace(self._token, _REDACTED)
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-length", "content-type"}
        }
        media_type = response.media_type or content_type.split(";", 1)[0].strip()
        return Response(
            content=body,
            status_code=response.status_code,
            headers=headers,
            media_type=media_type or None,
        )


def _is_text(content_type: str) -> bool:
    """Return whether this content type is safe to rewrite as text."""
    lowered = content_type.lower()
    return any(lowered.startswith(prefix) for prefix in _TEXT_TYPES)
