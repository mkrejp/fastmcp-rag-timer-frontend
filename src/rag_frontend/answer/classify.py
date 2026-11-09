"""Label facts as relevant, homonym, or noise. No model is called."""

from __future__ import annotations

import re

from rag_frontend.answer.parsers import Fact, mark_centroids


def classify_facts(
    facts: list[Fact],
    *,
    entity: re.Pattern[str],
    region_prefixes: tuple[str, ...] = (),
) -> tuple[list[Fact], list[Fact], bool]:
    """Split facts and note when the first search hit lacks the entity.

    ``obce.json`` rows match only an explicit kraj prefix from the query.
    With no prefix, every such row is a homonym and the answer says the
    region was not named.
    """
    mark_centroids(facts)
    ranking_error = False
    relevant: list[Fact] = []
    noise: list[Fact] = []
    for index, fact in enumerate(facts):
        blob = "\n".join((fact.title, fact.address, fact.text, fact.note))
        if fact.kind == "obec":
            _label_obec(fact, region_prefixes)
        elif fact.note in {"jen vektorová podobnost", "0 shod"}:
            fact.label = "noise"
        elif entity.search(blob) is None:
            fact.label = "noise"
            if index == 0:
                fact.note = "chyba řazení"
                ranking_error = True
        else:
            fact.label = "relevant"
            if fact.kind == "building" and not fact.image_ids:
                fact.note = fact.note or "obrázek nemá"
        if fact.label == "relevant":
            relevant.append(fact)
        else:
            noise.append(fact)
    return relevant[:5], noise, ranking_error


def _label_obec(fact: Fact, prefixes: tuple[str, ...]) -> None:
    """Apply the municipality homonym rule for every Czech kraj prefix."""
    obec_id = fact.obec_id.upper()
    if prefixes and any(obec_id.startswith(prefix) for prefix in prefixes):
        fact.label = "relevant"
        fact.note = ""
        return
    fact.label = "homonym"
    fact.note = "kraj není v dotazu" if not prefixes else "jiná obec stejného jména"
