"""Query variants from a small alias list. No model is called."""

from __future__ import annotations

import re

# Default until Marek supplies a fuller gazetteer.
# Vršovice in Prague historically Wrschowitz; Nusle is the neighbouring quarter.
ALIASES: dict[str, tuple[str, ...]] = {
    "vršovice": ("Vršovice", "Wrschowitz", "Nusle"),
    "vrsovice": ("Vršovice", "Wrschowitz", "Nusle"),
}

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
_WORD = re.compile(r"[0-9A-Za-zÁČĎÉĚÍŇÓŘŠŤÚŮÝŽáčďéěíňóřšťúůýž-]+")
_PRAGUE = re.compile(r"praha|prague|seces|činžov|cinzov|architekt|wrschowitz", re.I)


def expand_query(query: str) -> list[str]:
    """Return up to five search strings for ``query``.

    The first capitalized or aliased token is the entity. The next content
    words are the theme. Historical names come only from ``ALIASES``.
    """
    entity, extras, theme = _entity(query)
    variants = [entity]
    if theme:
        variants.append(f"{entity} {theme}")
    variants.append(f"{entity} činžovní dům architekt")
    variants.extend(extras)
    unique: list[str] = []
    for item in variants:
        cleaned = " ".join(item.split())
        if cleaned and cleaned.casefold() not in {value.casefold() for value in unique}:
            unique.append(cleaned)
    return unique[:5]


def entity_pattern(query: str) -> re.Pattern[str]:
    """Regex that matches the entity and its aliases."""
    entity, extras, _theme = _entity(query)
    parts = [re.escape(entity), *(re.escape(item) for item in extras)]
    stem = entity[:5]
    if len(stem) >= 4:
        parts.append(re.escape(stem))
    return re.compile("|".join(parts), re.IGNORECASE)


def prague_scoped(query: str) -> bool:
    """Return whether the query is about Prague rather than a same-named village.

    Default: Praha, secese, a tenement, an architect, or Wrschowitz means
    Prague (obec id prefix CZ010). Otherwise the kraj is unknown.
    """
    return _PRAGUE.search(query) is not None


def _entity(query: str) -> tuple[str, tuple[str, ...], str]:
    """Pick the place name, alias variants, and remaining theme words."""
    lowered = query.casefold()
    for key, names in ALIASES.items():
        if key in lowered:
            words = [
                word
                for word in _WORD.findall(query)
                if word.casefold() not in _STOP and word.casefold() not in key
            ]
            return names[0], names[1:], " ".join(words[:3])
    words = [word for word in _WORD.findall(query) if word.casefold() not in _STOP]
    if not words:
        text = query.strip() or "dotaz"
        return text, (), ""
    return words[0], (), " ".join(words[1:3])
