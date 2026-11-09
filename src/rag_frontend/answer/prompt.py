"""Short Czech synthesis prompt and the no-model template."""

from __future__ import annotations

import json

from rag_frontend.answer.parsers import Fact

SYSTEM_PROMPT = (
    "Piš česky jen z dodaného JSON. Nic nedoplňuj. "
    "Každá věta má filename a doc_id. "
    "Tučná první věta řekne, kolik toho server ví a jak je to jisté. "
    "Pak odrážka na objekt: název, adresa, rok, lidé, confidence, zdroj, GPS. "
    "Pak obrázky (má nebo nemá, centroid versus bod). "
    "Pak odstavec Šum. Homonymum nepatří mezi jisté objekty. "
    "Nevymýšlej URL."
)


def user_payload(
    query: str,
    variants: list[str],
    relevant: list[Fact],
    noise: list[Fact],
) -> str:
    """Compact JSON for the single synthesis call. No raw OCR."""
    return json.dumps(
        {
            "query": query,
            "variants": variants,
            "objects": [item.to_dict() for item in relevant],
            "noise": [item.to_dict() for item in noise[:8]],
        },
        ensure_ascii=False,
    )


def template_answer(query: str, relevant: list[Fact], noise: list[Fact]) -> str:
    """Czech answer used when the model is not called."""
    if not relevant:
        head = f"**K dotazu „{query}“ server nemá relevantní záznam.**"
    else:
        head = f"**K dotazu „{query}“ server eviduje {len(relevant)} záznamů.**"
    lines = [head, ""]
    for fact in relevant:
        bits = [fact.title]
        if fact.address and fact.address not in fact.title:
            bits.append(fact.address)
        if fact.year:
            bits.append(fact.year)
        if fact.people:
            bits.append(fact.people)
        if fact.confidence is not None:
            bits.append(f"confidence {fact.confidence}")
        if fact.gps:
            kind = "centroid" if fact.gps_kind == "centroid" else "bod"
            bits.append(f"GPS {fact.gps[0]}, {fact.gps[1]} ({kind})")
        if fact.note:
            bits.append(fact.note)
        bits.append(f"zdroj {fact.filename} doc_id={fact.doc_id}")
        lines.append("- " + "; ".join(bits))
    if noise:
        lines.extend(["", "Šum:"])
        for fact in noise[:8]:
            lines.append(
                f"- {fact.label}: {fact.title} ({fact.filename})"
                + (f" — {fact.note}" if fact.note else "")
            )
    elif relevant:
        lines.extend(["", "Šum: žádný označený."])
    return "\n".join(lines)
