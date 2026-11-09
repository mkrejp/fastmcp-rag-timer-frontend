"""Parsing tests for MCP tool text."""

from __future__ import annotations

import json

from rag_frontend.mcp_client.parse import parse_catalog, parse_images, parse_search

_DOC = "11111111-1111-1111-1111-111111111111"
_IMAGE = "0123456789abcdef"


def test_parse_search_passage_with_image() -> None:
    """A text hit that names a stored picture keeps the image id."""
    text = (
        "Source: scan.pdf (source_type=pdf_image) "
        f"doc_id={_DOC} page_number=2 image_id={_IMAGE} "
        "url=https://example.test/images/x\n"
        "Content: a chapel on the hill\n"
    )
    view = parse_search(text)
    assert view.error is None
    assert view.raw is None
    assert len(view.passages) == 1
    passage = view.passages[0]
    assert passage.source == "scan.pdf"
    assert passage.source_type == "pdf_image"
    assert passage.doc_id == _DOC
    assert passage.page_number == "2"
    assert passage.image_id == _IMAGE
    assert passage.content == "a chapel on the hill"


def test_parse_search_empty_and_error() -> None:
    """Empty hits and tool errors stay out of the passage list."""
    empty = parse_search("No relevant facts found.")
    pictures = parse_search("No relevant images found.")
    disabled = parse_search("IMAGE_EMBEDDING_ENABLED is false. Set it to true.")
    error = parse_search("Error executing RAG search: down")
    assert empty.message == "No relevant facts found."
    assert pictures.message == "No relevant images found."
    assert disabled.message is not None
    assert disabled.message.startswith("IMAGE_EMBEDDING_ENABLED")
    assert error.error == "Error executing RAG search: down"
    assert error.passages == ()


def test_parse_search_raw_when_shape_is_unknown() -> None:
    """Unstructured tool text is returned for the page to show as-is."""
    view = parse_search("not a passage")
    assert view.raw == "not a passage"
    assert view.passages == ()


def test_parse_catalog_drops_private_fields() -> None:
    """Catalog rows keep public fields and drop hashes and paths."""
    text = json.dumps(
        [
            {
                "doc_id": _DOC,
                "filename": "notes.txt",
                "mime": "text/plain",
                "sha256": "abc",
                "uploaded_at": "2026-01-01T00:00:00+00:00",
                "chunk_count": 2,
                "status": "ready",
                "original_path": "/data/secret",
            }
        ]
    )
    view = parse_catalog(text)
    assert view.error is None
    assert view.documents[0]["filename"] == "notes.txt"
    assert view.documents[0]["chunk_count"] == 2
    assert "sha256" not in view.documents[0]
    assert "original_path" not in view.documents[0]


def test_parse_catalog_empty_sentence() -> None:
    """The server's empty catalog sentence becomes a message."""
    view = parse_catalog("No documents stored.")
    assert view.documents == ()
    assert view.message == "No documents stored."


def test_parse_images_drops_path_and_upstream_url() -> None:
    """Picture metadata omits disk paths, hashes, and the upstream URL."""
    text = json.dumps(
        [
            {
                "image_id": _IMAGE,
                "sha256": "ff",
                "file_path": "/data/rag/x.png",
                "page_number": 4,
                "width": 80,
                "height": 60,
                "mime": "image/png",
                "ocr_text": "obec",
                "ocr_confidence": 90.5,
                "url": "https://example.test/images/x",
            }
        ]
    )
    view = parse_images(text)
    image = view.images[0]
    assert image.image_id == _IMAGE
    assert image.page_number == 4
    assert image.width == 80
    assert image.ocr_text == "obec"
    assert image.ocr_confidence == 90.5
    payload = image.to_dict()
    assert "sha256" not in payload
    assert "file_path" not in payload
    assert "url" not in payload


def test_parse_images_missing_document() -> None:
    """A missing document is an error, not a picture list."""
    view = parse_images(f"Document not found: {_DOC}")
    assert view.error == f"Document not found: {_DOC}"
    assert view.images == ()
