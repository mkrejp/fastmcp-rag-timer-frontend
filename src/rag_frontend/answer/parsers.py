"""Turn MCP text into structured facts. No model is called."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_COORDS = re.compile(r"(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)")
_IMAGE_PATH = re.compile(r"/images/([0-9a-f-]{36})/([a-f0-9]{16})", re.I)


@dataclass
class Fact:
    """One candidate object or noise row."""

    kind: str
    title: str
    text: str
    doc_id: str
    filename: str
    page: str | None = None
    image_ids: list[str] = field(default_factory=list)
    gps: tuple[float, float] | None = None
    gps_kind: str = ""
    confidence: float | None = None
    address: str = ""
    year: str = ""
    people: str = ""
    sources: list[str] = field(default_factory=list)
    obec_id: str = ""
    label: str = "relevant"
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        """Serialize the fact for the JSON API and the synthesis prompt."""
        return {
            "kind": self.kind,
            "title": self.title,
            "text": self.text[:280],
            "doc_id": self.doc_id,
            "filename": self.filename,
            "page": self.page,
            "image_ids": self.image_ids,
            "gps": list(self.gps) if self.gps else None,
            "gps_kind": self.gps_kind,
            "confidence": self.confidence,
            "address": self.address,
            "year": self.year,
            "people": self.people,
            "sources": self.sources,
            "obec_id": self.obec_id,
            "label": self.label,
            "note": self.note,
        }


def facts_from_passage(
    *,
    filename: str,
    doc_id: str,
    page: str | None,
    image_id: str | None,
    content: str,
) -> list[Fact]:
    """Parse one search block into a building, postcard, village, or plain hit."""
    if "building-hints" in filename or content.strip().startswith("## "):
        parsed = parse_building_hints(filename, doc_id, content)
        if parsed:
            return parsed
    if _is_obec(content, filename):
        return [_obec_fact(filename, doc_id, content)]
    if _is_akon(content, filename):
        return [_akon_fact(filename, doc_id, page, image_id, content)]
    return [
        Fact(
            kind="passage",
            title=filename,
            text=content.strip(),
            doc_id=doc_id,
            filename=filename,
            page=page,
            image_ids=[image_id] if image_id else [],
        )
    ]


def parse_building_hints(filename: str, doc_id: str, text: str) -> list[Fact]:
    """Split ``##`` sections and read the address, GPS, and confidence fields."""
    chunks = re.split(r"\n(?=## )", text.strip())
    facts: list[Fact] = []
    for chunk in chunks:
        if not chunk.startswith("##"):
            continue
        lines = chunk.splitlines()
        title = lines[0].lstrip("#").strip()
        address = _field(chunk, "Adresa")
        note = _field(chunk, "Poznámka")
        images = _image_ids(chunk)
        facts.append(
            Fact(
                kind="building",
                title=title,
                text=note or address or title,
                doc_id=doc_id,
                filename=filename,
                image_ids=images,
                gps=_coords(_field(chunk, "Souřadnice")),
                gps_kind="point" if _coords(_field(chunk, "Souřadnice")) else "",
                confidence=_float(_field(chunk, "Confidence")),
                address=address,
                people=_field(chunk, "Stavitel") or _people(chunk),
                sources=_urls(_section(chunk, "Zdroje")),
                note="" if images or "Obrázky:" in chunk else "obrázek nemá",
            )
        )
    return facts


def mark_centroids(facts: list[Fact]) -> None:
    """Mark repeated postcard coordinates as a city centroid."""
    groups: dict[tuple[float, float], set[str]] = {}
    for fact in facts:
        if fact.kind == "akon" and fact.gps:
            key = (round(fact.gps[0], 5), round(fact.gps[1], 5))
            groups.setdefault(key, set()).add(fact.address or fact.title)
    for fact in facts:
        if fact.kind != "akon" or not fact.gps:
            continue
        key = (round(fact.gps[0], 5), round(fact.gps[1], 5))
        fact.gps_kind = "centroid" if len(groups.get(key, ())) > 1 else "point"


def _akon_fact(
    filename: str,
    doc_id: str,
    page: str | None,
    image_id: str | None,
    content: str,
) -> Fact:
    """Read postcard fields from an AKON chunk."""
    place = _field(content, "Misto") or filename
    gps = _coords(_field(content, "Souradnice"))
    return Fact(
        kind="akon",
        title=place,
        text=content.strip(),
        doc_id=doc_id,
        filename=filename,
        page=page,
        image_ids=[image_id] if image_id else _image_ids(content),
        gps=gps,
        gps_kind="point" if gps else "",
        address=place,
        year=_field(content, "Rok"),
    )


def _obec_fact(filename: str, doc_id: str, content: str) -> Fact:
    """Read one municipality record."""
    return Fact(
        kind="obec",
        title=_field(content, "name") or filename,
        text=content.strip(),
        doc_id=doc_id,
        filename=filename,
        obec_id=_field(content, "id"),
        address=_field(content, "name"),
    )


def _is_akon(content: str, filename: str) -> bool:
    """Return whether the chunk is an AKON postcard."""
    return filename.lower().startswith("akon_") or "akon_id:" in content.lower()


def _is_obec(content: str, filename: str) -> bool:
    """Return whether the chunk is an ``obce.json`` record."""
    lowered = content.lower()
    return "obce.json" in filename or "nationalcode:" in lowered


def _field(text: str, name: str) -> str:
    """Return the remainder of the first ``Name:`` line."""
    match = re.search(rf"(?im)^{re.escape(name)}:\s*(.+)$", text)
    return match.group(1).strip() if match else ""


def _section(text: str, name: str) -> str:
    """Return the lines that follow a ``Name:`` heading until the next field."""
    pattern = rf"(?ims)^{re.escape(name)}:\s*(.+?)(?:\n[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ][^:\n]+:|\Z)"
    match = re.search(pattern, text)
    return match.group(1).strip() if match else ""


def _coords(value: str) -> tuple[float, float] | None:
    """Parse ``lat, lon`` or return None."""
    match = _COORDS.search(value)
    if match is None:
        return None
    return float(match.group(1)), float(match.group(2))


def _float(value: str) -> float | None:
    """Parse a confidence value."""
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return None


def _people(text: str) -> str:
    """Collect a builder or architect mention from free text."""
    match = re.search(r"(?i)(stavitelé?|architekt)\s+([^.;\n]+)", text)
    return match.group(0).strip() if match else ""


def _urls(text: str) -> list[str]:
    """Return http(s) links."""
    return re.findall(r"https?://\S+", text)


def _image_ids(text: str) -> list[str]:
    """Return image ids embedded in ``/images/{doc}/{id}`` URLs."""
    return [image_id for _doc, image_id in _IMAGE_PATH.findall(text)]
