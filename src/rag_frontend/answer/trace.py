"""Safe step log for one answer. Never stores secrets."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from rag_frontend.mcp_client.session import McpToolClient

_DROP = {"authorization", "cookie", "api_key", "token", "secret", "password", "prompt"}
_Hook = Callable[[dict[str, Any]], Awaitable[None]]


class Trace:
    """Append-only steps for the signed-in debug frame."""

    def __init__(self, hook: _Hook | None = None) -> None:
        """Start the clock. ``hook`` receives each event for live streaming."""
        self.events: list[dict[str, Any]] = []
        self._hook = hook
        self._t0 = time.perf_counter()

    async def event(self, step: str, **detail: Any) -> None:
        """Record one step. Strings are shortened and secret-like keys dropped."""
        item = {
            "step": step,
            "t_ms": round((time.perf_counter() - self._t0) * 1000, 1),
            **_clean(detail),
        }
        self.events.append(item)
        if self._hook is not None:
            await self._hook(dict(item))


async def traced_mcp(
    client: McpToolClient,
    trace: Trace,
    name: str,
    arguments: dict[str, Any],
    measure: Callable[[str], int] | None = None,
) -> str:
    """Log a tool call before it starts and again when it returns.

    The start line is emitted before ``call_tool`` awaits, so a live stream
    can show the request while the server is still working.
    """
    await trace.event("mcp", phase="start", tool=name, arguments=arguments)
    started = time.perf_counter()
    try:
        text = await client.call_tool(name, arguments)
    except Exception as exc:
        await trace.event(
            "mcp",
            phase="end",
            tool=name,
            hits=0,
            error=type(exc).__name__,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        raise
    failed = text.startswith("Error ") or text.startswith("Document not found")
    hits = 0 if failed or measure is None else measure(text)
    detail: dict[str, Any] = {
        "phase": "end",
        "tool": name,
        "hits": hits,
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
    }
    if failed:
        detail["error"] = True
    await trace.event("mcp", **detail)
    return text


def _clean(detail: dict[str, Any]) -> dict[str, Any]:
    """Remove secret keys and clip long strings."""
    cleaned: dict[str, Any] = {}
    for key, value in detail.items():
        if key.lower() in _DROP or "token" in key.lower() or "secret" in key.lower():
            continue
        cleaned[key] = _clip(value)
    return cleaned


def _clip(value: Any) -> Any:
    """Shorten strings and nested dicts."""
    if isinstance(value, str):
        text = value.replace("Bearer ", "")
        return text if len(text) <= 160 else text[:157] + "..."
    if isinstance(value, dict):
        return _clean(value)
    if isinstance(value, list):
        return [_clip(item) for item in value[:12]]
    return value
