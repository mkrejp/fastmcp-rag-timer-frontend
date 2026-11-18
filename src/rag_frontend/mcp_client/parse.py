"""Turn MCP tool text into catalog rows and search passages."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

_HEADER = re.compile(
    r"^Source: (?P<source>.*) \(source_type=(?P<source_type>[^)]+)\)(?P<rest>.*)$"
)
_TOKEN = re.compile(r"(page_image_id|page_url|doc_id|page_number|image_id|url)=(\S+)")
_NO_FACTS = "No relevant facts found."
_NO_IMAGES = "No relevant images found."
_NO_DOCUMENTS = "No documents stored."
_IMAGE_DISABLED = "IMAGE_EMBEDDING_ENABLED is false."
_PUBLIC_FIELDS = (
    "doc_id",
    "filename",
    "mime",
    "uploaded_at",
    "chunk_count",
    "status",
)
_CONTEXT_SECTIONS = (
    "catalog",
    "lifecycle",
    "geo",
    "versions",
    "locality_links",
    "related_documents",
    "neighbor_chunks",
)
_HIT_SCALAR_FIELDS = (
    "doc_id",
    "parent_document",
    "source_type",
    "page_number",
    "page_image_id",
    "image_id",
    "score",
    "content",
    "chunk_index",
    "caption",
    "verification_status",
    "confidence",
    "lat",
    "lon",
    "geo_source",
    "doc_status",
    "superseded_by",
    "supersedes",
    "correction_note",
)
_DROP_KEYS = frozenset({"sha256", "file_path", "url"})


@dataclass(frozen=True)
class Passage:
    """One retrieved block from ``search_domain_knowledge``."""

    source: str
    source_type: str
    content: str
    doc_id: str | None = None
    page_number: str | None = None
    image_id: str | None = None
    url: str | None = None
    page_image_id: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        """Serialize the passage for the JSON API."""
        return {
            "source": self.source,
            "source_type": self.source_type,
            "content": self.content,
            "doc_id": self.doc_id,
            "page_number": self.page_number,
            "image_id": self.image_id,
            "url": self.url,
            "page_image_id": self.page_image_id,
        }


@dataclass(frozen=True)
class SearchView:
    """Parsed search tool output."""

    passages: tuple[Passage, ...]
    message: str | None
    raw: str | None
    error: str | None


@dataclass(frozen=True)
class ImageMetaView:
    """Public fields for one stored picture."""

    image_id: str
    page_number: int | None
    width: int | None
    height: int | None
    mime: str | None
    ocr_text: str | None
    ocr_confidence: float | None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the image metadata for the JSON API."""
        return {
            "image_id": self.image_id,
            "page_number": self.page_number,
            "width": self.width,
            "height": self.height,
            "mime": self.mime,
            "ocr_text": self.ocr_text,
            "ocr_confidence": self.ocr_confidence,
        }


@dataclass(frozen=True)
class ImageListView:
    """Parsed ``get_document_images`` or image-search output."""

    images: tuple[ImageMetaView, ...]
    message: str | None
    error: str | None


@dataclass(frozen=True)
class CatalogView:
    """Parsed catalog tool output."""

    documents: tuple[dict[str, Any], ...]
    message: str | None
    error: str | None


@dataclass(frozen=True)
class ContextHit:
    """One enriched hit from ``search_with_context`` or catalog context wrap."""

    fields: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Serialize the hit for the JSON API."""
        return dict(self.fields)


@dataclass(frozen=True)
class ContextSearchView:
    """Parsed JSON list from a context search tool."""

    hits: tuple[ContextHit, ...]
    message: str | None
    error: str | None


@dataclass(frozen=True)
class DocumentContextView:
    """Parsed ``get_document_context`` JSON object."""

    context: dict[str, Any] | None
    error: str | None


def parse_search(text: str) -> SearchView:
    """Parse ``search_domain_knowledge`` text into passages.

    Tool errors and the empty-hit sentence stay structured. Text that does
    not match the ``Source`` / ``Content`` blocks is returned as ``raw``.
    """
    stripped = text.strip()
    if stripped.startswith("Error "):
        return SearchView((), None, None, stripped)
    if stripped in {_NO_FACTS, _NO_IMAGES} or stripped.startswith(_IMAGE_DISABLED):
        return SearchView((), stripped, None, None)
    blocks = [block for block in text.split("\n---\n") if block.strip()]
    passages: list[Passage] = []
    for block in blocks:
        passage = _parse_block(block)
        if passage is None:
            return SearchView((), None, text, None)
        passages.append(passage)
    if not passages:
        return SearchView((), None, text, None)
    return SearchView(tuple(passages), None, None, None)


def parse_images(text: str) -> ImageListView:
    """Parse ``get_document_images`` JSON into public image fields.

    ``sha256``, ``file_path``, and the upstream ``url`` are dropped. The page
    loads bytes through this app so the bearer token stays on the server.
    """
    stripped = text.strip()
    if stripped.startswith("Error ") or stripped.startswith("Document not found"):
        return ImageListView((), None, stripped)
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return ImageListView((), stripped, None)
    if not isinstance(payload, list):
        return ImageListView((), stripped, None)
    images = tuple(
        _shape_image(entry)
        for entry in payload
        if isinstance(entry, dict) and entry.get("image_id")
    )
    return ImageListView(images, None, None)


def parse_catalog(text: str) -> CatalogView:
    """Parse ``list_documents`` text into public catalog fields.

    Disk paths and ``sha256`` are dropped. A non-JSON body that is not the
    empty-catalog sentence is returned as ``message`` so the page can show it.
    """
    stripped = text.strip()
    if stripped.startswith("Error "):
        return CatalogView((), None, stripped)
    if stripped == _NO_DOCUMENTS:
        return CatalogView((), stripped, None)
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return CatalogView((), stripped, None)
    if not isinstance(payload, list):
        return CatalogView((), stripped, None)
    documents = tuple(
        _shape_document(entry) for entry in payload if isinstance(entry, dict)
    )
    return CatalogView(documents, None, None)


def _parse_block(block: str) -> Passage | None:
    """Parse one ``Source`` / ``Content`` block, or return None."""
    stripped = block.strip("\n")
    header, separator, body = stripped.partition("\n")
    if not separator or not body.startswith("Content: "):
        return None
    match = _HEADER.match(header)
    if match is None:
        return None
    fields = {name: value for name, value in _TOKEN.findall(match.group("rest"))}
    content = body[len("Content: ") :]
    if content.endswith("\n"):
        content = content[:-1]
    return Passage(
        source=match.group("source"),
        source_type=match.group("source_type"),
        content=content,
        doc_id=fields.get("doc_id"),
        page_number=fields.get("page_number"),
        image_id=fields.get("image_id"),
        url=fields.get("url"),
        page_image_id=fields.get("page_image_id"),
    )


def _shape_image(entry: dict[str, Any]) -> ImageMetaView:
    """Keep the image fields the page is allowed to show."""
    page = entry.get("page_number")
    width = entry.get("width")
    height = entry.get("height")
    confidence = entry.get("ocr_confidence")
    ocr = entry.get("ocr_text")
    mime = entry.get("mime")
    return ImageMetaView(
        image_id=str(entry["image_id"]),
        page_number=int(page) if page is not None else None,
        width=int(width) if width is not None else None,
        height=int(height) if height is not None else None,
        mime=None if mime is None else str(mime),
        ocr_text=None if ocr is None else str(ocr),
        ocr_confidence=float(confidence) if confidence is not None else None,
    )


def _shape_document(entry: dict[str, Any]) -> dict[str, Any]:
    """Keep the catalog fields the page is allowed to show."""
    shaped: dict[str, Any] = {}
    for key in _PUBLIC_FIELDS:
        if key not in entry:
            shaped[key] = None
            continue
        value = entry[key]
        if key == "chunk_count" and value is not None:
            shaped[key] = int(value)
        else:
            shaped[key] = None if value is None else str(value)
    return shaped


def parse_context_search(text: str) -> ContextSearchView:
    """Parse ``search_with_context`` / catalog-search JSON into public hits.

    Tool ``Error …`` strings become ``error``. Empty lists are valid. Non-JSON
    bodies that are not errors become a ``message`` so the page can show them.
    """
    stripped = text.strip()
    if stripped.startswith("Error "):
        return ContextSearchView((), None, stripped)
    if stripped in {_NO_FACTS, _NO_DOCUMENTS}:
        return ContextSearchView((), stripped, None)
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return ContextSearchView((), stripped, None)
    if not isinstance(payload, list):
        return ContextSearchView((), stripped, None)
    hits = tuple(
        ContextHit(_shape_context_hit(entry))
        for entry in payload
        if isinstance(entry, dict)
    )
    return ContextSearchView(hits, None, None)


def parse_document_context(text: str) -> DocumentContextView:
    """Parse ``get_document_context`` JSON into a public context bundle."""
    stripped = text.strip()
    if stripped.startswith("Document not found"):
        return DocumentContextView(None, stripped)
    if stripped.startswith("Error "):
        return DocumentContextView(None, stripped)
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return DocumentContextView(None, stripped)
    if not isinstance(payload, dict):
        return DocumentContextView(None, stripped)
    return DocumentContextView(_shape_document_context(payload), None)


def _shape_context_hit(entry: dict[str, Any]) -> dict[str, Any]:
    """Keep hit scalars and context sections; drop paths and hashes."""
    shaped: dict[str, Any] = {}
    for key in _HIT_SCALAR_FIELDS:
        if key not in entry:
            continue
        value = entry[key]
        if value is None or value == "":
            continue
        shaped[key] = value
    for key in _CONTEXT_SECTIONS:
        if key not in entry:
            continue
        shaped[key] = _sanitize_context_value(entry[key])
    # Catalog-only rows from ``search_catalog_documents`` without wrap.
    if "catalog" not in shaped and any(field in entry for field in _PUBLIC_FIELDS):
        catalog = _shape_document(entry)
        if any(value is not None for value in catalog.values()):
            shaped["catalog"] = {
                key: value for key, value in catalog.items() if value is not None
            }
            if "doc_id" not in shaped and catalog.get("doc_id"):
                shaped["doc_id"] = catalog["doc_id"]
    return shaped


def _shape_document_context(entry: dict[str, Any]) -> dict[str, Any]:
    """Sanitize one document context bundle for the browser."""
    shaped: dict[str, Any] = {}
    doc_id = entry.get("doc_id")
    if doc_id:
        shaped["doc_id"] = str(doc_id)
    for key in _CONTEXT_SECTIONS:
        if key not in entry:
            continue
        shaped[key] = _sanitize_context_value(entry[key])
    return shaped


def _sanitize_context_value(value: Any) -> Any:
    """Recursively drop secrets/paths from nested context JSON."""
    if isinstance(value, dict):
        return {
            key: _sanitize_context_value(item)
            for key, item in value.items()
            if key not in _DROP_KEYS
        }
    if isinstance(value, list):
        return [_sanitize_context_value(item) for item in value]
    return value
