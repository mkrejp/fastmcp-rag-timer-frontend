"""Build one Czech answer: retrieve, classify, then at most one chat call."""

from __future__ import annotations

import time
from typing import Any

from rag_frontend.answer.cache import BudgetDecision, TokenBudget, TtlCache
from rag_frontend.answer.classify import classify_facts
from rag_frontend.answer.expand import entity_pattern, expand_query, prague_scoped
from rag_frontend.answer.prompt import template_answer, user_payload
from rag_frontend.answer.retrieve import bind_cache, retrieve
from rag_frontend.answer.synthesize import LlmClient, estimate_usd
from rag_frontend.answer.trace import Trace
from rag_frontend.config import Settings
from rag_frontend.mcp_client.session import McpToolClient

_ANSWERS = TtlCache(600)
_BUDGET = TokenBudget()


def configure_answer_cache(ttl_seconds: int) -> None:
    """Apply the configured TTL to retrieval and answer caches."""
    global _ANSWERS
    _ANSWERS = TtlCache(ttl_seconds)
    bind_cache(ttl_seconds)


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
        prague=prague_scoped(query),
    )
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
    )
    images = [
        {"image_id": image_id, "doc_id": fact.doc_id, "caption": fact.title}
        for fact in relevant
        for image_id in fact.image_ids
    ]
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
) -> tuple[str, int, int, str, bool, str]:
    """Return markdown, token counts, model, whether chat ran, and why it did not."""
    if not relevant:
        await trace.event("llm", called=False, reason="žádný relevantní hit")
        return (
            template_answer(query, relevant, noise),
            0,
            0,
            "",
            False,
            "žádný relevantní hit",
        )
    if not use_llm or not settings.answer_use_llm:
        await trace.event("llm", called=False, reason="režim bez LLM")
        return template_answer(query, relevant, noise), 0, 0, "", False, "režim bez LLM"
    if not settings.llm_api_key:
        await trace.event("llm", called=False, reason="chybí klíč")
        return template_answer(query, relevant, noise), 0, 0, "", False, "chybí klíč"
    decision: BudgetDecision = _BUDGET.check(email or "anonymous")
    if not decision.allowed:
        await trace.event("llm", called=False, reason=decision.reason)
        return template_answer(query, relevant, noise), 0, 0, "", False, decision.reason
    payload = user_payload(query, variants, relevant, noise)
    await trace.event(
        "llm",
        called=True,
        reason="syntéza",
        prompt_preview=payload[:120],
    )
    started = time.perf_counter()
    try:
        result = await LlmClient(settings).complete(payload)
    except Exception as exc:
        await trace.event(
            "error",
            where="llm",
            error=type(exc).__name__,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return template_answer(query, relevant, noise), 0, 0, "", False, "chyba modelu"
    await trace.event(
        "llm",
        model=result.model,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        usd=estimate_usd(result.model, result.prompt_tokens, result.completion_tokens),
        duration_ms=round((time.perf_counter() - started) * 1000, 1),
        http_status=result.http_status,
        fallback=result.calls > 1,
        calls=result.calls,
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
