"""Short Czech synthesis prompt and the no-model template."""

from __future__ import annotations

import json

from rag_frontend.answer.parsers import Fact

SYSTEM_PROMPT = (
    "Piš česky souvislými odstavci, jen z dodaného JSON. Nic nedoplňuj. "
    "První odstavec tučně shrň, co server ví a jak je to jisté. "
    "Další odstavce: každá budova nebo místo má adresu, autora, rok, "
    "confidence a GPS, pokud v JSON jsou. U tvrzení cituj [1], [2] "
    "podle pořadí objects. "
    "Pak odstavec o obrázcích: má je, nebo nemá, a jestli je GPS bod nebo centroid. "
    "Poslední odstavec nadpis 'Co server neví / omezení' a homonyma i šum. "
    "Když poznámka zní 'kraj není v dotazu', napiš výslovně, že kraj v dotazu "
    "chybí a všechny obce stejného jména jsou homonyma. "
    "Homonymum nepleť do jistých vět. Nevymýšlej URL ani doc_id."
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
            "objects": [item.to_dict() for item in relevant[:5]],
            "images": [
                {"image_id": image_id, "caption": item.title[:120]}
                for item in relevant[:5]
                for image_id in item.image_ids
            ],
            "noise": [
                {
                    "title": item.title,
                    "filename": item.filename,
                    "label": item.label,
                    "note": item.note,
                }
                for item in noise[:8]
            ],
        },
        ensure_ascii=False,
    )


def template_answer(query: str, relevant: list[Fact], noise: list[Fact]) -> str:
    """Czech prose used when the model is not called."""
    if not relevant:
        opening = f"**K dotazu „{query}“ server nic jistého neví.**"
    else:
        opening = (
            f"**K dotazu „{query}“ server ví o {len(relevant)} "
            "záznamech.** Jistota je jen taková, jakou nesou uvedené zdroje."
        )
    paragraphs = [opening]
    for index, fact in enumerate(relevant, start=1):
        sentence = fact.title
        if fact.address and fact.address not in fact.title:
            sentence += f", {fact.address}"
        if fact.people:
            sentence += f", {fact.people}"
        if fact.year:
            sentence += f", rok {fact.year}"
        if fact.confidence is not None:
            sentence += f", confidence {fact.confidence}"
        if fact.gps:
            kind = "centroid města" if fact.gps_kind == "centroid" else "bod"
            sentence += f". GPS {fact.gps[0]}, {fact.gps[1]} ({kind})"
        if fact.note:
            sentence += f". {fact.note}"
        sentence += f". [{index}]"
        paragraphs.append(sentence)
    if any(fact.image_ids for fact in relevant):
        paragraphs.append("K některým záznamům server má uložený obrázek.")
    elif relevant:
        paragraphs.append("K těmto záznamům server obrázek nemá.")
    limits = ["**Co server neví / omezení**"]
    if any(fact.note == "kraj není v dotazu" for fact in noise):
        limits.append(
            "Kraj není v dotazu, takže všechny záznamy z obce.json jsou homonyma."
        )
    if noise:
        described = "; ".join(
            f"{fact.title} ({fact.filename})"
            + (f", {fact.note}" if fact.note else "")
            for fact in noise[:8]
        )
        limits.append(
            "Mimo odpověď zůstaly stejnojmenné nebo nesouvisející záznamy: "
            + described
            + "."
        )
    else:
        limits.append("Další označený šum v tomto dotazu není.")
    paragraphs.append(" ".join(limits))
    return "\n\n".join(paragraphs)
