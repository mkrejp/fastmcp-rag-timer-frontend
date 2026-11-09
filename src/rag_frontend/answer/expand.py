"""Query variants from a small alias list. No model is called."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

_ALIAS_PATH = Path(__file__).with_name("aliases.json")
# Czech NUTS-3 prefixes used by obce.json ids. Longer okres codes still match.
REGIONS: tuple[tuple[str, str], ...] = (
    ("praha", "CZ010"),
    ("prague", "CZ010"),
    ("prag", "CZ010"),
    ("středočesk", "CZ020"),
    ("stredocesk", "CZ020"),
    ("jihočesk", "CZ031"),
    ("jihocesk", "CZ031"),
    ("plzeň", "CZ032"),
    ("plzen", "CZ032"),
    ("karlovar", "CZ041"),
    ("ústeck", "CZ042"),
    ("usteck", "CZ042"),
    ("liberec", "CZ051"),
    ("hradec králové", "CZ052"),
    ("hradec kralove", "CZ052"),
    ("pardubic", "CZ053"),
    ("budějovic", "CZ031"),
    ("budejovic", "CZ031"),
    ("jihlav", "CZ063"),
    ("vysočin", "CZ063"),
    ("vysocin", "CZ063"),
    ("jihomorav", "CZ064"),
    ("brno", "CZ064"),
    ("brünn", "CZ064"),
    ("brunn", "CZ064"),
    ("olomouc", "CZ071"),
    ("zlín", "CZ072"),
    ("zlin", "CZ072"),
    ("moravskoslez", "CZ080"),
    ("ostrav", "CZ080"),
)


def load_aliases() -> dict[str, tuple[str, ...]]:
    """Read the alias file. Neighbours are not implied."""
    raw = json.loads(_ALIAS_PATH.read_text(encoding="utf-8"))
    return {
        str(key).casefold(): tuple(str(item) for item in values)
        for key, values in raw.items()
    }


ALIASES = load_aliases()

_STOP = {
    "co",
    "ví",
    "vi",
    "server",
    "o",
    "lokalitě",
    "lokalite",
    "a",
    "zde",
    "je",
    "na",
    "ve",
    "v",
    "se",
    "to",
    "jak",
    "kde",
    "pro",
    "do",
    "od",
}
_WORD = re.compile(r"[0-9A-Za-zÁČĎÉĚÍŇÓŘŠŤÚŮÝŽáčďéěíňóřšťúůýžÄÖÜäöü-]+")


def expand_query(query: str) -> list[str]:
    """Return up to eight search strings for ``query``.

    The first capitalized or aliased token is the entity. The next content
    words are the theme. Historical names come only from ``ALIASES``.
    """
    entity, extras, theme = _entity(query)
    variants = [entity]
    if theme:
        variants.append(f"{entity} {theme}")
    variants.append(f"{entity} činžovní dům architekt")
    variants.extend(extras)
    with_plain = list(variants)
    for item in variants:
        plain = strip_diacritics(item)
        if plain.casefold() != item.casefold():
            with_plain.append(plain)
    return _unique(with_plain)[:8]


def strip_diacritics(text: str) -> str:
    """Return ``text`` without Czech or German diacritics."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def region_prefixes(query: str) -> tuple[str, ...]:
    """Map region and city names in ``query`` to obec-id prefixes.

    An empty result means the query names no kraj, so every ``obce.json``
    hit stays a homonym.
    """
    folded = strip_diacritics(query).casefold()
    found: list[str] = []
    for name, prefix in REGIONS:
        if strip_diacritics(name).casefold() in folded and prefix not in found:
            found.append(prefix)
    return tuple(found)


def _unique(variants: list[str]) -> list[str]:
    """Drop blank and repeated variants, keeping order."""
    unique: list[str] = []
    seen: set[str] = set()
    for item in variants:
        cleaned = " ".join(item.split())
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            unique.append(cleaned)
    return unique


def _display(key: str) -> str:
    """Capitalize an alias key for use as the search entity."""
    return " ".join(part[:1].upper() + part[1:] for part in key.split())


def entity_pattern(query: str) -> re.Pattern[str]:
    """Regex that matches the entity and its aliases."""
    entity, extras, _theme = _entity(query)
    parts = [re.escape(entity), *(re.escape(item) for item in extras)]
    plain = strip_diacritics(entity)
    if plain.casefold() != entity.casefold():
        parts.append(re.escape(plain))
    stem = entity[:5]
    if len(stem) >= 4:
        parts.append(re.escape(stem))
    return re.compile("|".join(parts), re.IGNORECASE)


def _entity(query: str) -> tuple[str, tuple[str, ...], str]:
    """Pick the place name, alias variants, and remaining theme words."""
    lowered = query.casefold()
    ranked = sorted(
        ALIASES.items(),
        key=lambda item: max(len(item[0]), *(len(name) for name in item[1])),
        reverse=True,
    )
    for key, names in ranked:
        needles = (key, *(name.casefold() for name in names))
        if not any(needle in lowered for needle in needles):
            continue
        blocked = set(key.split())
        for name in names:
            blocked.update(name.casefold().split())
        words = [
            word
            for word in _WORD.findall(query)
            if word.casefold() not in _STOP and word.casefold() not in blocked
        ]
        return _display(key), names, " ".join(words[:3])
    words = [word for word in _WORD.findall(query) if word.casefold() not in _STOP]
    if not words:
        text = query.strip() or "dotaz"
        return text, (), ""
    return words[0], (), " ".join(words[1:3])
