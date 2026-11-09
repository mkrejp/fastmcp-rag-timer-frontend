"""Build one Czech answer: retrieve, classify, then at most one chat call."""

from __future__ import annotations

from typing import Any

from rag_frontend.answer.cache import BudgetDecision, TokenBudget, TtlCache
from rag_frontend.answer.classify import classify_facts
from rag_frontend.answer.expand import entity_pattern, expand_query, prague_scoped
from rag_frontend.answer.prompt import template_answer, user_payload
from rag_frontend.answer.retrieve import bind_cache, retrieve
from rag_frontend.answer.synthesize import LlmClient
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
) -> dict[str, Any]:
    """Return the answer payload. The model is skipped when nothing relevant remains."""
    variants = expand_query(query)
    facts, timings, version = await retrieve(client, query, variants)
    relevant, noise, ranking_error = classify_facts(
        facts,
        entity=entity_pattern(query),
        prague=prague_scoped(query),
    )
    if ranking_error and noise:
        noise[0].note = noise[0].note or "chyba řazení"
    normalized = " ".join(query.casefold().split())
    llm_flag = int(use_llm and settings.answer_use_llm)
    cache_key = f"{version}:{llm_flag}:{normalized}"
    cached = _ANSWERS.get(cache_key)
    if isinstance(cached, dict):
        cached_debug = dict(cached["debug"])
        cached_debug["cache_hit"] = True
        return {**cached, "debug": cached_debug}
    text, prompt_tokens, completion_tokens, model, called, reason = await _compose(
        settings,
        query,
        variants,
        relevant,
        noise,
        email=email,
        use_llm=use_llm,
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
) -> tuple[str, int, int, str, bool, str]:
    """Return markdown, token counts, model, whether chat ran, and why it did not."""
    if not relevant:
        return (
            template_answer(query, relevant, noise),
            0,
            0,
            "",
            False,
            "žádný relevantní hit",
        )
    if not use_llm or not settings.answer_use_llm:
        return template_answer(query, relevant, noise), 0, 0, "", False, "režim bez LLM"
    if not settings.llm_api_key:
        return template_answer(query, relevant, noise), 0, 0, "", False, "chybí klíč"
    decision: BudgetDecision = _BUDGET.check(email or "anonymous")
    if not decision.allowed:
        return template_answer(query, relevant, noise), 0, 0, "", False, decision.reason
    payload = user_payload(query, variants, relevant, noise)
    result = await LlmClient(settings).complete(payload)
    _BUDGET.add(email or "anonymous", result.prompt_tokens, result.completion_tokens)
    return (
        result.text,
        result.prompt_tokens,
        result.completion_tokens,
        result.model,
        True,
        "",
    )
