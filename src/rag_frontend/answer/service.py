"""Build one Czech answer: retrieve, classify, then at most one chat call."""

from __future__ import annotations

import time
from typing import Any

from rag_frontend.answer.cache import BudgetDecision, TokenBudget, TtlCache
from rag_frontend.answer.classify import classify_facts
from rag_frontend.answer.expand import entity_pattern, expand_query, region_prefixes
from rag_frontend.answer.pages import attach_page_previews
from rag_frontend.answer.pages import bind_cache as bind_page_cache
from rag_frontend.answer.prompt import template_answer, user_payload
from rag_frontend.answer.retrieve import bind_cache, retrieve
from rag_frontend.answer.synthesize import LlmClient, estimate_usd
from rag_frontend.answer.trace import Trace, traced_mcp
from rag_frontend.config import Settings
from rag_frontend.mcp_client.parse import Passage, parse_search
from rag_frontend.mcp_client.session import McpToolClient

_ANSWERS = TtlCache(600)
_BUDGET = TokenBudget()


def _caption_from_passage(passage: Passage) -> str:
    """Prefer search Content, else filename + page, else source name."""
    content = (passage.content or "").strip()
    if content:
        return content.split("\n", 1)[0][:120]
    source = (passage.source or "").strip()
    page = (passage.page_number or "").strip()
    if source and page:
        return f"{source}, strana {page}"[:120]
    return (source or "Obrázek")[:120]


async def _attach_images(
    client: McpToolClient,
    query: str,
    relevant: list[Any],
    trace: Trace,
) -> list[dict[str, str]]:
    """Build the answer gallery from query-ranked ``search_images`` hits.

    Hits are kept even when their ``doc_id`` is not among the text-classified
    relevant facts. Matching hits are also copied onto those facts for the
    synthesis prompt. Ids already attached during text retrieval (for example
    an AKON postcard) fill remaining gallery slots. Document image catalogs
    are never dumped here.
    """
    text = await traced_mcp(
        client,
        trace,
        "search_images",
        {"query": query, "limit": 5, "hybrid": True},
        measure=lambda body: len(parse_search(body).passages),
    )
    view = parse_search(text)
    by_doc = {fact.doc_id: fact for fact in relevant if fact.doc_id}
    gallery: list[dict[str, str]] = []
    seen: set[str] = set()
    for passage in view.passages:
        if not passage.image_id or passage.image_id in seen:
            continue
        seen.add(passage.image_id)
        caption = _caption_from_passage(passage)
        gallery.append(
            {
                "image_id": passage.image_id,
                "doc_id": passage.doc_id or "",
                "caption": caption,
            }
        )
        fact = by_doc.get(passage.doc_id or "")
        if fact is not None and passage.image_id not in fact.image_ids:
            fact.image_ids.append(passage.image_id)
        if len(gallery) >= 5:
            break
    for fact in relevant:
        if len(gallery) >= 5:
            break
        for image_id in fact.image_ids:
            if image_id in seen:
                continue
            seen.add(image_id)
            gallery.append(
                {
                    "image_id": image_id,
                    "doc_id": fact.doc_id or "",
                    "caption": (fact.title or fact.filename or "Obrázek")[:120],
                }
            )
            if len(gallery) >= 5:
                break
    return gallery


async def _llm_skipped(trace: Trace, reason: str) -> None:
    """Record that the model was not called, as a finished LLM step."""
    await trace.event(
        "llm",
        phase="end",
        called=False,
        reason=reason,
        duration_ms=0,
        hits=0,
    )


def configure_answer_cache(ttl_seconds: int) -> None:
    """Apply the configured TTL to retrieval and answer caches."""
    global _ANSWERS
    _ANSWERS = TtlCache(ttl_seconds)
    bind_cache(ttl_seconds)
    bind_page_cache(ttl_seconds)


async def answer_query(
    client: McpToolClient,
    settings: Settings,
    query: str,
    *,
    email: str,
    use_llm: bool,
    trace: Trace | None = None,
) -> dict[str, Any]:
    """Return the answer payload. The model is skipped when nothing relevant remains."""
    log = trace or Trace()
    await log.event("query", text=query[:160], chars=len(query))
    variants = expand_query(query)
    await log.event("expand", variants=variants, count=len(variants))
    facts, timings, version = await retrieve(client, query, variants, log)
    relevant, noise, ranking_error = classify_facts(
        facts,
        entity=entity_pattern(query),
        region_prefixes=region_prefixes(query),
    )
    images = await _attach_images(client, query, relevant, log)
    await attach_page_previews(client, query, relevant, log)
    for fact in noise:
        fact.page_image_id = ""
        fact.highlights = []
    if ranking_error and noise:
        noise[0].note = noise[0].note or "chyba řazení"
    reasons: dict[str, int] = {}
    for fact in noise:
        reasons[fact.label] = reasons.get(fact.label, 0) + 1
    await log.event(
        "classify",
        kept=len(relevant),
        dropped=len(noise),
        reasons=reasons,
        parsers=["building", "akon", "obec", "passage"],
    )
    normalized = " ".join(query.casefold().split())
    llm_flag = int(use_llm and settings.answer_use_llm)
    cache_key = f"{version}:{llm_flag}:{normalized}"
    cached = _ANSWERS.get(cache_key)
    if isinstance(cached, dict):
        await log.event("cache", target="answer", result="hit")
        cached_debug = dict(cached["debug"])
        cached_debug["cache_hit"] = True
        cached_debug["trace"] = log.events
        return {**cached, "debug": cached_debug}
    await log.event("cache", target="answer", result="miss")
    text, prompt_tokens, completion_tokens, model, called, reason = await _compose(
        settings,
        query,
        variants,
        relevant,
        noise,
        email=email,
        use_llm=use_llm,
        trace=log,
        images=images,
    )
    payload = {
        "answer_markdown": text,
        "objects": [fact.to_dict() for fact in relevant],
        "noise": [fact.to_dict() for fact in noise],
        "images": images,
        "debug": {
            "variants": variants,
            "timings_ms": timings,
            "llm_called": called,
            "llm_calls": 1 if called else 0,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "model": model,
            "cache_hit": False,
            "reason": reason,
            "trace": log.events,
        },
    }
    _ANSWERS.set(cache_key, payload)
    return payload


async def _compose(
    settings: Settings,
    query: str,
    variants: list[str],
    relevant: list[Any],
    noise: list[Any],
    *,
    email: str,
    use_llm: bool,
    trace: Trace,
    images: list[dict[str, str]] | None = None,
) -> tuple[str, int, int, str, bool, str]:
    """Return markdown, token counts, model, whether chat ran, and why it did not."""
    gallery = images or []
    if not relevant:
        await _llm_skipped(trace, "žádný relevantní hit")
        return (
            template_answer(query, relevant, noise, images=gallery),
            0,
            0,
            "",
            False,
            "žádný relevantní hit",
        )
    if not use_llm or not settings.answer_use_llm:
        await _llm_skipped(trace, "režim bez LLM")
        return (
            template_answer(query, relevant, noise, images=gallery),
            0,
            0,
            "",
            False,
            "režim bez LLM",
        )
    if not settings.llm_api_key:
        await _llm_skipped(trace, "chybí klíč")
        return (
            template_answer(query, relevant, noise, images=gallery),
            0,
            0,
            "",
            False,
            "chybí klíč",
        )
    decision: BudgetDecision = _BUDGET.check(email or "anonymous")
    if not decision.allowed:
        await _llm_skipped(trace, decision.reason)
        return (
            template_answer(query, relevant, noise, images=gallery),
            0,
            0,
            "",
            False,
            decision.reason,
        )
    payload = user_payload(query, variants, relevant, noise, images=gallery)
    await trace.event("llm", phase="start", model=settings.llm_model)
    started = time.perf_counter()
    try:
        result = await LlmClient(settings).complete(payload)
    except Exception as exc:
        await trace.event(
            "llm",
            phase="end",
            called=False,
            error=type(exc).__name__,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            hits=0,
        )
        return (
            template_answer(query, relevant, noise, images=gallery),
            0,
            0,
            "",
            False,
            "chyba modelu",
        )
    await trace.event(
        "llm",
        phase="end",
        called=True,
        model=result.model,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        usd=estimate_usd(result.model, result.prompt_tokens, result.completion_tokens),
        duration_ms=round((time.perf_counter() - started) * 1000, 1),
        http_status=result.http_status,
        fallback=result.calls > 1,
        calls=result.calls,
        hits=1,
    )
    _BUDGET.add(email or "anonymous", result.prompt_tokens, result.completion_tokens)
    return (
        result.text,
        result.prompt_tokens,
        result.completion_tokens,
        result.model,
        True,
        "",
    )
