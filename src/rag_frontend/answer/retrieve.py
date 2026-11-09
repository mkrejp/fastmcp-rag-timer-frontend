"""Parallel MCP reads for one answer. No model is called."""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from typing import Any

from rag_frontend.answer.cache import TtlCache
from rag_frontend.answer.expand import entity_pattern
from rag_frontend.answer.parsers import Fact, facts_from_passage, parse_building_hints
from rag_frontend.answer.trace import Trace
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
    trace: Trace,
) -> tuple[list[Fact], dict[str, float], str]:
    """Search variants, then read priority non-postcard full texts."""
    timings: dict[str, float] = {}
    started = time.perf_counter()
    catalog = await _catalog(client, trace)
    timings["list_documents"] = round((time.perf_counter() - started) * 1000, 1)
    version = _version(catalog)
    semaphore = asyncio.Semaphore(3)

    async def _one(variant: str) -> list[Fact]:
        async with semaphore:
            call_started = time.perf_counter()
            text = await client.call_tool(
                "search_domain_knowledge",
                {"query": variant, "limit": 8},
            )
        facts = _facts_from_search(text)
        await trace.event(
            "mcp",
            tool="search_domain_knowledge",
            arguments={"query": variant, "limit": 8},
            hits=len(facts),
            duration_ms=round((time.perf_counter() - call_started) * 1000, 1),
        )
        return facts

    search_started = time.perf_counter()
    batches = await asyncio.gather(*[_one(variant) for variant in variants])
    timings["search_domain_knowledge"] = round(
        (time.perf_counter() - search_started) * 1000, 1
    )
    facts = _dedup([fact for batch in batches for fact in batch])
    snippets, elapsed, loaded, hint_docs = await _fulltext(
        client, query, catalog, facts, trace
    )
    timings["get_document_text"] = elapsed
    pattern = entity_pattern(query)
    verified = reverify_facts(facts, loaded, pattern)
    verified = [
        item
        for item in verified
        if not (item.doc_id in hint_docs and item.kind == "building")
    ]
    return _dedup(verified + snippets), timings, version


async def _catalog(client: McpToolClient, trace: Trace) -> list[dict[str, Any]]:
    """Return the cached catalog and record hit or miss."""
    cached = _CATALOG.get("catalog")
    if isinstance(cached, list):
        await trace.event("cache", target="catalog", result="hit", hits=len(cached))
        return cached
    await trace.event("cache", target="catalog", result="miss")
    started = time.perf_counter()
    text = await client.call_tool("list_documents", {"since": ""})
    view = parse_catalog(text)
    documents = [] if view.error else list(view.documents)
    await trace.event(
        "mcp",
        tool="list_documents",
        arguments={"since": ""},
        hits=len(documents),
        duration_ms=round((time.perf_counter() - started) * 1000, 1),
        error=bool(view.error),
    )
    if not view.error:
        _CATALOG.set("catalog", documents)
    return documents


def select_fulltext_rows(
    catalog: list[dict[str, Any]],
    search_doc_ids: set[str],
    pattern: re.Pattern[str],
    *,
    limit: int = 8,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Prefer search hits, building-hints, and obce.json over other files.

    Priority rows are always kept. The cap applies only to further filename
    matches. AKON postcards and empty chunk counts are skipped.
    """
    priority: list[dict[str, Any]] = []
    extras: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in catalog:
        filename = str(row.get("filename") or "")
        doc_id = str(row.get("doc_id") or "")
        chunks = row.get("chunk_count")
        if (
            not doc_id
            or doc_id in seen
            or filename.lower().startswith("akon_")
            or not isinstance(chunks, int)
            or chunks <= 0
        ):
            continue
        seen.add(doc_id)
        preferred = (
            doc_id in search_doc_ids
            or "building-hints" in filename
            or "obce.json" in filename
        )
        if preferred:
            priority.append(row)
        elif pattern.search(filename):
            extras.append(row)
    if len(priority) >= limit:
        chosen = priority
    else:
        chosen = priority + extras[: limit - len(priority)]
    priority_ids = {id(row) for row in priority}
    extra_ids = {
        str(row["doc_id"]) for row in chosen if id(row) not in priority_ids
    }
    return chosen, extra_ids


def reverify_facts(
    facts: list[Fact],
    fulltext: dict[str, str],
    pattern: re.Pattern[str],
) -> list[Fact]:
    """Drop non-postcard hits whose full document never names the entity."""
    checked: list[Fact] = []
    for fact in facts:
        if fact.kind == "akon" or not fact.doc_id or fact.doc_id not in fulltext:
            checked.append(fact)
            continue
        if pattern.search(fulltext[fact.doc_id]) is None:
            fact.label = "noise"
            fact.note = "jen vektorová podobnost"
        checked.append(fact)
    return checked


async def _fulltext(
    client: McpToolClient,
    query: str,
    catalog: list[dict[str, Any]],
    found: list[Fact],
    trace: Trace,
) -> tuple[list[Fact], float, dict[str, str], set[str]]:
    """Read priority non-AKON documents and record files with no entity match."""
    pattern = entity_pattern(query)
    search_ids = {fact.doc_id for fact in found if fact.doc_id}
    chosen, extra_ids = select_fulltext_rows(catalog, search_ids, pattern)
    started = time.perf_counter()
    snippets: list[Fact] = []
    loaded: dict[str, str] = {}
    hint_docs: set[str] = set()
    absent: list[dict[str, object]] = []
    for row in chosen:
        doc_id = str(row["doc_id"])
        filename = str(row.get("filename") or "")
        text, failed = await _load_text(client, doc_id, trace)
        if failed:
            continue
        loaded[doc_id] = text
        if "building-hints" in filename:
            hint_docs.add(doc_id)
            sections = parse_building_hints(filename, doc_id, text, entity=pattern)
            matched = bool(sections)
            window_chars = sum(len(section.text) for section in sections)
            snippets.extend(sections)
        else:
            match = pattern.search(text)
            window = _window(text, pattern) if match else ""
            matched = match is not None
            window_chars = len(window)
            if matched:
                snippets.extend(
                    facts_from_passage(
                        filename=filename,
                        doc_id=doc_id,
                        page=None,
                        image_id=None,
                        content=window,
                    )
                )
        await trace.event(
            "fulltext",
            filename=filename,
            match=matched,
            window_chars=window_chars,
        )
        if not matched and doc_id in extra_ids:
            absent.append({"filename": filename, "matches": 0})
            snippets.append(
                Fact(
                    kind="absent",
                    title=filename,
                    text="",
                    doc_id=doc_id,
                    filename=filename,
                    label="noise",
                    note="0 shod",
                )
            )
    await trace.event("absent_corpora", files=absent, count=len(absent))
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    return snippets, elapsed, loaded, hint_docs


async def _load_text(
    client: McpToolClient,
    doc_id: str,
    trace: Trace,
) -> tuple[str, bool]:
    """Return cached or freshly read full text, and whether the read failed."""
    cached = _FULLTEXT.get(doc_id)
    if isinstance(cached, str):
        await trace.event("cache", target="fulltext", result="hit", doc_id=doc_id)
        return cached, False
    started = time.perf_counter()
    text = await client.call_tool("get_document_text", {"doc_id": doc_id})
    missing = text.startswith("Document not found")
    failed = text.startswith("Error ") or missing
    await trace.event(
        "mcp",
        tool="get_document_text",
        arguments={"doc_id": doc_id},
        hits=0 if failed else 1,
        duration_ms=round((time.perf_counter() - started) * 1000, 1),
        error=failed,
    )
    if not failed:
        _FULLTEXT.set(doc_id, text)
    return text, failed


def _facts_from_search(text: str) -> list[Fact]:
    """Convert a search tool body into facts."""
    view = parse_search(text)
    facts: list[Fact] = []
    for passage in view.passages:
        built = facts_from_passage(
            filename=passage.source,
            doc_id=passage.doc_id or "",
            page=passage.page_number,
            image_id=passage.image_id,
            content=passage.content,
        )
        for fact in built:
            if passage.page_image_id:
                fact.page_image_id = passage.page_image_id
        facts.extend(built)
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
