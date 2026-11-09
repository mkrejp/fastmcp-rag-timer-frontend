"""In-process TTL cache and the daily token budget."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


class TtlCache:
    """Remember values until ``ttl_seconds`` elapses."""

    def __init__(self, ttl_seconds: int) -> None:
        """Store the lifetime used for every entry."""
        self._ttl = ttl_seconds
        self._items: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        """Return a fresh value, or None when the key is missing or stale."""
        found = self._items.get(key)
        if found is None:
            return None
        expires, value = found
        if expires < time.monotonic():
            del self._items[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        """Store ``value`` under ``key``."""
        self._items[key] = (time.monotonic() + self._ttl, value)


@dataclass
class BudgetDecision:
    """Whether a synthesis call is still inside the daily cap."""

    allowed: bool
    reason: str


class TokenBudget:
    """Per-user and global token caps. Counts reset at UTC midnight or restart."""

    def __init__(
        self,
        user_tokens: int = 50_000,
        user_calls: int = 40,
        global_tokens: int = 500_000,
    ) -> None:
        """Store the caps from the plan."""
        self._user_tokens = user_tokens
        self._user_calls = user_calls
        self._global_tokens = global_tokens
        self._users: dict[str, tuple[str, int, int]] = {}
        self._global: tuple[str, int] = ("", 0)

    def check(self, email: str) -> BudgetDecision:
        """Return whether ``email`` may spend another synthesis."""
        day = _today()
        _day, tokens, calls = self._users.get(email, (day, 0, 0))
        if _day != day:
            tokens, calls = 0, 0
        global_day, global_tokens = self._global
        if global_day != day:
            global_tokens = 0
        if calls >= self._user_calls or tokens >= self._user_tokens:
            return BudgetDecision(False, "denní limit uživatele")
        if global_tokens >= self._global_tokens:
            return BudgetDecision(False, "globální rozpočet")
        return BudgetDecision(True, "")

    def add(self, email: str, prompt_tokens: int, completion_tokens: int) -> None:
        """Record one synthesis against the user and the global cap."""
        day = _today()
        spent = prompt_tokens + completion_tokens
        _day, tokens, calls = self._users.get(email, (day, 0, 0))
        if _day != day:
            tokens, calls = 0, 0
        self._users[email] = (day, tokens + spent, calls + 1)
        global_day, global_tokens = self._global
        if global_day != day:
            global_tokens = 0
        self._global = (day, global_tokens + spent)


def _today() -> str:
    """Return the current UTC date."""
    return datetime.now(UTC).date().isoformat()
