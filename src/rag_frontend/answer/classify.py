"""Label facts as relevant, homonym, or noise. No model is called."""

from __future__ import annotations

import re

from rag_frontend.answer.parsers import Fact, mark_centroids


def classify_facts(
    facts: list[Fact],
    *,
    entity: re.Pattern[str],
    prague: bool,
) -> tuple[list[Fact], list[Fact], bool]:
    """Split facts and note when the first search hit lacks the entity.

    Homonym default: a Prague-scoped query (Praha, secese, tenement, architect,
    Wrschowitz) keeps municipality ids with prefix ``CZ010`` only. Any other
    prefix is another village. If the query names no region, every
    ``obce.json`` hit is a homonym with the note ``kraj není v dotazu``, so
    the page does not pretend one of the three Vršovice is the right one.
    """
    mark_centroids(facts)
    ranking_error = False
    relevant: list[Fact] = []
    noise: list[Fact] = []
    for index, fact in enumerate(facts):
        blob = "\n".join((fact.title, fact.address, fact.text, fact.note))
        if fact.kind == "obec":
            _label_obec(fact, prague)
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


def _label_obec(fact: Fact, prague: bool) -> None:
    """Apply the municipality homonym default."""
    obec_id = fact.obec_id.upper()
    if prague and obec_id.startswith("CZ010"):
        fact.label = "relevant"
        return
    fact.label = "homonym"
    if not prague:
        fact.note = "kraj není v dotazu"
    elif not obec_id.startswith("CZ010"):
        fact.note = "jiná obec stejného jména"
