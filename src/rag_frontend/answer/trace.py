"""Safe step log for one answer. Never stores secrets."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

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
