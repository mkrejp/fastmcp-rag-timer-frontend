"""Parallel MCP reads for one answer. No model is called."""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from typing import Any

from rag_frontend.answer.cache import TtlCache
from rag_frontend.answer.expand import entity_pattern
from rag_frontend.answer.parsers import Fact, facts_from_passage
from rag_frontend.mcp_client.parse import parse_catalog, parse_search
from rag_frontend.mcp_client.session import McpToolClient

_CATALOG = TtlCache(600)
_FULLTEXT = TtlCache(600)


def bind_cache(ttl_seconds: int) -> None:
    """Point the module caches at ``ttl_seconds``."""
    global _CATALOG, _FULLTEXT
    _CATALOG = TtlCache(ttl_seconds)
    _FULLTEXT = TtlCache(ttl_seconds)


async def retrieve(
    client: McpToolClient,
    query: str,
    variants: list[str],
) -> tuple[list[Fact], dict[str, float], str]:
    """Search variants, then read at most eight non-postcard full texts."""
    timings: dict[str, float] = {}
    started = time.perf_counter()
    catalog = await _catalog(client)
    timings["list_documents"] = round((time.perf_counter() - started) * 1000, 1)
    version = _version(catalog)
    semaphore = asyncio.Semaphore(3)
    search_started = time.perf_counter()

    async def _one(variant: str) -> list[Fact]:
        async with semaphore:
            text = await client.call_tool(
                "search_domain_knowledge",
                {"query": variant, "limit": 8},
            )
        return _facts_from_search(text)

    batches = await asyncio.gather(*[_one(variant) for variant in variants])
    timings["search_domain_knowledge"] = round(
        (time.perf_counter() - search_started) * 1000, 1
    )
    facts = _dedup([fact for batch in batches for fact in batch])
    extra = await _fulltext(client, query, catalog, facts)
    timings["get_document_text"] = extra[1]
    return _dedup(facts + extra[0]), timings, version


async def _catalog(client: McpToolClient) -> list[dict[str, Any]]:
    """Return the cached catalog."""
    cached = _CATALOG.get("catalog")
    if isinstance(cached, list):
        return cached
    text = await client.call_tool("list_documents", {"since": ""})
    view = parse_catalog(text)
    if view.error:
        return []
    _CATALOG.set("catalog", list(view.documents))
    return list(view.documents)


async def _fulltext(
    client: McpToolClient,
    query: str,
    catalog: list[dict[str, Any]],
    found: list[Fact],
) -> tuple[list[Fact], float]:
    """Read up to eight non-AKON documents that likely mention the entity."""
    pattern = entity_pattern(query)
    chosen: list[dict[str, Any]] = []
    seen = {fact.doc_id for fact in found}
    for row in catalog:
        filename = str(row.get("filename") or "")
        doc_id = str(row.get("doc_id") or "")
        if not doc_id or filename.lower().startswith("akon_"):
            continue
        interesting = (
            "building-hints" in filename
            or "obce.json" in filename
            or pattern.search(filename) is not None
            or doc_id in seen
        )
        if interesting:
            chosen.append(row)
        if len(chosen) >= 8:
            break
    started = time.perf_counter()
    snippets: list[Fact] = []
    for row in chosen:
        doc_id = str(row["doc_id"])
        filename = str(row.get("filename") or "")
        text = _FULLTEXT.get(doc_id)
        if not isinstance(text, str):
            text = await client.call_tool("get_document_text", {"doc_id": doc_id})
            missing = text.startswith("Document not found")
            if not text.startswith("Error ") and not missing:
                _FULLTEXT.set(doc_id, text)
        if pattern.search(text):
            window = _window(text, pattern)
            snippets.extend(
                facts_from_passage(
                    filename=filename,
                    doc_id=doc_id,
                    page=None,
                    image_id=None,
                    content=window,
                )
            )
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    return snippets, elapsed


def _facts_from_search(text: str) -> list[Fact]:
    """Convert a search tool body into facts."""
    view = parse_search(text)
    facts: list[Fact] = []
    for passage in view.passages:
        facts.extend(
            facts_from_passage(
                filename=passage.source,
                doc_id=passage.doc_id or "",
                page=passage.page_number,
                image_id=passage.image_id,
                content=passage.content,
            )
        )
    return facts


def _dedup(facts: list[Fact]) -> list[Fact]:
    """Drop repeated document, page, image, and text combinations."""
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[Fact] = []
    for fact in facts:
        image = ",".join(fact.image_ids)
        digest = hashlib.sha256(fact.text.encode("utf-8")).hexdigest()[:12]
        key = (fact.doc_id, fact.page or "", image, digest)
        if key in seen:
            continue
        seen.add(key)
        unique.append(fact)
    return unique


def _window(text: str, pattern: re.Pattern[str]) -> str:
    """Return about 300 characters around the first entity match."""
    match = pattern.search(text)
    if match is None:
        return text[:300]
    start = max(0, match.start() - 150)
    return text[start : match.end() + 150]


def _version(catalog: list[dict[str, Any]]) -> str:
    """Fingerprint the catalog so answer cache entries expire when it changes."""
    latest = ""
    for row in catalog:
        uploaded = str(row.get("uploaded_at") or "")
        if uploaded > latest:
            latest = uploaded
    return f"{len(catalog)}:{latest}"
