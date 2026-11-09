"""PDF page thumbnails and highlight boxes. No model is called."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from rag_frontend.answer.cache import TtlCache
from rag_frontend.answer.parsers import Fact
from rag_frontend.answer.trace import Trace
from rag_frontend.mcp_client.session import McpCallError, McpToolClient

_PAGE_ID = re.compile(
    r"^page:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}):([1-9][0-9]{0,4})$",
    re.IGNORECASE,
)
_MISSING = ("unknown tool", "tool not found")
_MAX_PREVIEWS = 5
_HIGHLIGHTS = TtlCache(600)
_CAPABILITY = TtlCache(600)
_CAP_KEY = "page-tools"


def bind_cache(ttl_seconds: int) -> None:
    """Point highlight and capability caches at ``ttl_seconds``."""
    global _HIGHLIGHTS, _CAPABILITY
    _HIGHLIGHTS = TtlCache(ttl_seconds)
    _CAPABILITY = TtlCache(ttl_seconds)


def parse_page_image_id(value: str) -> tuple[str, int] | None:
    """Parse ``page:{doc_id}:{page}``. ``page`` is 1-based."""
    match = _PAGE_ID.fullmatch(value.strip())
    if match is None:
        return None
    return match.group(1), int(match.group(2))


def is_page_image_id(value: str) -> bool:
    """Return whether ``value`` is a full-page image id."""
    return parse_page_image_id(value) is not None


def page_image_id_from_zero_based(doc_id: str, page_number: str | None) -> str | None:
    """Build a page id from Qdrant's 0-based ``page_number``.

    The id uses the next integer. A hit that already carries
    ``page_image_id`` must not be incremented again.
    """
    if page_number is None or re.fullmatch(r"\d+", page_number) is None:
        return None
    one_based = int(page_number) + 1
    if one_based > 99999:
        return None
    candidate = f"page:{doc_id}:{one_based}"
    if parse_page_image_id(candidate) is None:
        return None
    return candidate


def tools_blocked() -> bool:
    """Return whether a recent call showed the page tools are absent."""
    return _CAPABILITY.get(_CAP_KEY) is False


def note_supported() -> None:
    """Remember that this process has seen page image ids."""
    _CAPABILITY.set(_CAP_KEY, True)


def note_missing() -> None:
    """Remember that ``get_page_highlights`` is not on this server."""
    _CAPABILITY.set(_CAP_KEY, False)


def tool_missing(text: str) -> bool:
    """Return whether ``text`` says the highlight tool does not exist."""
    lowered = text.casefold()
    return any(phrase in lowered for phrase in _MISSING)


def parse_highlights(text: str) -> list[dict[str, float]]:
    """Keep normalized boxes. Coordinates outside 0–1 are dropped."""
    if not text or text.startswith("Error"):
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    boxes = payload.get("highlights") if isinstance(payload, dict) else None
    if not isinstance(boxes, list):
        return []
    cleaned: list[dict[str, float]] = []
    for box in boxes:
        if not isinstance(box, dict):
            continue
        try:
            coords = {key: float(box[key]) for key in ("x0", "y0", "x1", "y1")}
        except (KeyError, TypeError, ValueError):
            continue
        if not all(0.0 <= value <= 1.0 for value in coords.values()):
            continue
        if coords["x1"] <= coords["x0"] or coords["y1"] <= coords["y0"]:
            continue
        cleaned.append(coords)
    return cleaned[:20]


async def attach_page_previews(
    client: McpToolClient,
    query: str,
    relevant: list[Fact],
    trace: Trace,
) -> None:
    """Add highlight boxes for up to five PDF pages that name a page image.

    Hits without ``page_image_id`` stay as they are. A server that does not
    implement the tool is remembered so later queries do not call it again.
    """
    if tools_blocked():
        stripped = _clear_pages(relevant)
        if stripped:
            await trace.event(
                "page_image",
                ready=False,
                reason="nástroje chybí",
                duration_ms=0,
            )
        return
    chosen = _choose(relevant)
    if not chosen:
        return
    note_supported()
    for fact, doc_id, page in chosen:
        await trace.event(
            "page_image",
            filename=fact.filename,
            page_image_id=fact.page_image_id,
            page=page,
            ready=True,
        )
        missing = await _highlights(client, query, fact, doc_id, page, trace)
        if missing:
            _clear_pages(relevant)
            await trace.event(
                "page_image",
                ready=False,
                reason="nástroje chybí",
                duration_ms=0,
            )
            return


def _choose(relevant: list[Fact]) -> list[tuple[Fact, str, int]]:
    """Keep the first five distinct valid page images."""
    chosen: list[tuple[Fact, str, int]] = []
    seen: set[str] = set()
    for fact in relevant:
        parsed = parse_page_image_id(fact.page_image_id)
        if parsed is None or fact.page_image_id in seen or len(chosen) >= _MAX_PREVIEWS:
            fact.page_image_id = ""
            fact.highlights = []
            continue
        seen.add(fact.page_image_id)
        chosen.append((fact, parsed[0], parsed[1]))
    return chosen


def _clear_pages(facts: list[Fact]) -> int:
    """Drop page previews so the page falls back to the text source row."""
    cleared = 0
    for fact in facts:
        if fact.page_image_id or fact.highlights:
            cleared += 1
        fact.page_image_id = ""
        fact.highlights = []
    return cleared


async def _highlights(
    client: McpToolClient,
    query: str,
    fact: Fact,
    doc_id: str,
    page: int,
    trace: Trace,
) -> bool:
    """Fill ``fact.highlights``. Return True when the tool is absent."""
    key = f"{doc_id}:{page}:{' '.join(query.casefold().split())}"
    cached = _HIGHLIGHTS.get(key)
    if isinstance(cached, list):
        fact.highlights = cached
        await trace.event(
            "highlights",
            filename=fact.filename,
            page=page,
            boxes=len(cached),
            cache="hit",
            duration_ms=0,
        )
        return False
    started = time.perf_counter()
    try:
        text = await client.call_tool(
            "get_page_highlights",
            {"doc_id": doc_id, "page": page, "query": query},
        )
    except (McpCallError, TimeoutError, OSError) as exc:
        await trace.event(
            "highlights",
            filename=fact.filename,
            page=page,
            boxes=0,
            error=type(exc).__name__,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return False
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    if tool_missing(text):
        note_missing()
        await trace.event(
            "highlights",
            filename=fact.filename,
            page=page,
            boxes=0,
            error="nástroj chybí",
            duration_ms=elapsed,
        )
        return True
    boxes = parse_highlights(text)
    if not text.startswith("Error"):
        _HIGHLIGHTS.set(key, boxes)
    fact.highlights = boxes
    detail: dict[str, Any] = {
        "filename": fact.filename,
        "page": page,
        "boxes": len(boxes),
        "cache": "miss",
        "duration_ms": elapsed,
    }
    if text.startswith("Error"):
        detail["error"] = True
    await trace.event("highlights", **detail)
    return False
