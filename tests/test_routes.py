"""HTTP route tests with a stand-in MCP client."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from rag_frontend.api.routes import create_app
from rag_frontend.config import Settings
from rag_frontend.mcp_client.session import ImageBytes, McpCallError

_DOC = "11111111-1111-1111-1111-111111111111"
_IMAGE = "0123456789abcdef"
_PNG = b"\x89PNG\r\n\x1a\n"


class FakeClient:
    """Records tool calls and returns canned text or image bytes."""

    def __init__(
        self,
        tools: dict[str, str],
        images: dict[str, ImageBytes] | None = None,
        fail: set[str] | None = None,
    ) -> None:
        """Store canned results."""
        self.tools = tools
        self.images = images or {}
        self.fail = fail or set()
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Return canned text or raise a transport error."""
        self.calls.append((name, arguments))
        if name in self.fail:
            raise McpCallError("MCP call failed.")
        return self.tools[name]

    async def fetch_image(self, image_id: str) -> ImageBytes:
        """Return canned image bytes."""
        self.calls.append(("get_image", {"image_id": image_id}))
        return self.images[image_id]


def _settings(token: str = "secret") -> Settings:
    """Build settings that never leave the test process."""
    return Settings(
        mcp_server_url="http://127.0.0.1:9/mcp/",
        mcp_api_token=token,
        host="127.0.0.1",
        port=8000,
    )


def _hit() -> str:
    """One search block that points at a stored picture."""
    return (
        "Source: scan.pdf (source_type=pdf_chunk) "
        f"doc_id={_DOC} page_number=1 image_id={_IMAGE}\n"
        "Content: stored fact\n"
    )


def test_catalog_and_page_load() -> None:
    """The catalog route shapes rows and the page HTML is served."""
    client = FakeClient(
        {
            "list_documents": json.dumps(
                [
                    {
                        "doc_id": _DOC,
                        "filename": "notes.txt",
                        "mime": "text/plain",
                        "sha256": "hide",
                        "uploaded_at": "2026-01-02T00:00:00+00:00",
                        "chunk_count": 1,
                        "status": "ready",
                    }
                ]
            ),
            "get_document_images": json.dumps(
                [
                    {
                        "image_id": _IMAGE,
                        "page_number": 1,
                        "width": 40,
                        "height": 30,
                        "mime": "image/png",
                        "sha256": "hide",
                        "file_path": "/secret.png",
                    }
                ]
            ),
        }
    )
    http = TestClient(create_app(_settings(), client, forced_user="tester@example.com"))
    page = http.get("/")
    assert page.status_code == 200
    assert "Co o tom ví server" in page.text
    catalog = http.get("/api/catalog")
    assert catalog.status_code == 200
    row = catalog.json()["documents"][0]
    assert row["filename"] == "notes.txt"
    assert row["thumbnail_image_id"] == _IMAGE
    assert "sha256" not in row
    names = [name for name, _arguments in client.calls]
    assert names == ["list_documents", "get_document_images"]


def test_search_includes_picture_hits_by_default() -> None:
    """A question searches text and stored pictures, then returns both."""
    client = FakeClient(
        {
            "search_domain_knowledge": _hit(),
            "search_images": (
                "Source: scan.pdf (source_type=pdf_image) "
                f"doc_id={_DOC} image_id={_IMAGE}\n"
                "Content: chapel\n"
            ),
        }
    )
    http = TestClient(create_app(_settings(), client, forced_user="tester@example.com"))
    response = http.post("/api/search", json={"query": "chapel", "limit": 3})
    assert response.status_code == 200
    body = response.json()
    assert body["passages"][0]["content"] == "stored fact"
    assert body["passages"][0]["image_id"] == _IMAGE
    assert body["image_passages"][0]["source_type"] == "pdf_image"
    names = [name for name, _arguments in client.calls]
    assert names == ["search_domain_knowledge", "search_images"]


def test_search_can_skip_pictures() -> None:
    """Unchecking picture search does not call ``search_images``."""
    client = FakeClient({"search_domain_knowledge": "No relevant facts found."})
    http = TestClient(create_app(_settings(), client, forced_user="tester@example.com"))
    response = http.post(
        "/api/search",
        json={"query": "chapel", "include_images": False},
    )
    assert response.status_code == 200
    assert response.json()["message"] == "No relevant facts found."
    assert response.json()["image_passages"] == []
    assert [name for name, _arguments in client.calls] == ["search_domain_knowledge"]


def test_empty_query_does_not_call_mcp() -> None:
    """Blank questions are rejected before any tool call."""
    client = FakeClient({})
    http = TestClient(create_app(_settings(), client, forced_user="tester@example.com"))
    response = http.post("/api/search", json={"query": "   "})
    assert response.status_code == 400
    assert client.calls == []


def test_tool_error_string_is_http_502() -> None:
    """An MCP ``Error`` string does not become a passage."""
    client = FakeClient({"search_domain_knowledge": "Error executing RAG search: down"})
    http = TestClient(create_app(_settings(), client, forced_user="tester@example.com"))
    response = http.post("/api/search", json={"query": "chapel"})
    assert response.status_code == 502
    assert "down" in response.json()["detail"]
    assert [name for name, _arguments in client.calls] == ["search_domain_knowledge"]


def test_picture_search_failure_keeps_text_hits() -> None:
    """A failed picture search still returns the text passages."""
    client = FakeClient(
        {"search_domain_knowledge": _hit()},
        fail={"search_images"},
    )
    http = TestClient(create_app(_settings(), client, forced_user="tester@example.com"))
    response = http.post("/api/search", json={"query": "chapel"})
    assert response.status_code == 200
    body = response.json()
    assert body["passages"][0]["content"] == "stored fact"
    assert body["image_passages"] == []
    assert body["image_message"] == "MCP call failed."


def test_missing_token_refuses_query_routes() -> None:
    """Query routes answer 503 when no bearer token is configured."""
    http = TestClient(create_app(_settings(token=""), forced_user="tester@example.com"))
    assert http.get("/api/catalog").status_code == 503
    assert http.get("/").status_code == 200


def test_document_text_images_and_bytes() -> None:
    """Opening a document returns its text, picture list, and image bytes."""
    client = FakeClient(
        {
            "get_document_text": "full text",
            "get_document_images": json.dumps(
                [
                    {
                        "image_id": _IMAGE,
                        "sha256": "hide",
                        "file_path": "/secret.png",
                        "page_number": 3,
                        "width": 10,
                        "height": 12,
                        "mime": "image/png",
                        "url": "https://upstream.test/images/x",
                    }
                ]
            ),
        },
        images={_IMAGE: ImageBytes(_PNG, "image/png", None)},
    )
    http = TestClient(create_app(_settings(), client, forced_user="tester@example.com"))
    text = http.get(f"/api/documents/{_DOC}/text")
    images = http.get(f"/api/documents/{_DOC}/images")
    picture = http.get(f"/api/images/{_IMAGE}")
    assert text.json()["text"] == "full text"
    meta = images.json()["images"][0]
    assert meta["image_id"] == _IMAGE
    assert "file_path" not in meta
    assert "url" not in meta
    assert picture.status_code == 200
    assert picture.content == _PNG
    assert picture.headers["content-type"].startswith("image/png")


def test_runtime_token_is_removed_from_text_responses() -> None:
    """A configured bearer token never appears in HTML, JSON, or OpenAPI."""
    secret = "token-do-not-leak-9f3a"
    client = FakeClient(
        {
            "list_documents": f"Error list_documents: rejected {secret}",
            "search_domain_knowledge": (
                "Source: notes.txt (source_type=text_chunk)\n"
                f"Content: quoted {secret} from the store\n"
            ),
        },
        images={
            _IMAGE: ImageBytes(b"\x89PNG" + secret.encode() + b"END", "image/png", None)
        },
    )
    http = TestClient(
        create_app(
            _settings(token=secret),
            client,
            forced_user="tester@example.com",
        )
    )
    catalog = http.get("/api/catalog")
    search = http.post(
        "/api/search",
        json={"query": "chapel", "include_images": False},
    )
    page = http.get("/")
    script = http.get("/static/app.js")
    spec = http.get("/openapi.json")
    picture = http.get(f"/api/images/{_IMAGE}")
    for response in (catalog, search, page, script, spec):
        assert secret.encode() not in response.content
    assert b"[redacted]" in catalog.content
    assert b"[redacted]" in search.content
    assert picture.content == b"\x89PNG" + secret.encode() + b"END"


def test_missing_document_and_oversized_image() -> None:
    """Missing documents are 404 and oversized pictures explain themselves."""
    client = FakeClient(
        {"get_document_text": f"Document not found: {_DOC}"},
        images={
            _IMAGE: ImageBytes(
                None,
                None,
                "Image exceeds MCP inline limit (9000000 bytes).",
            )
        },
    )
    http = TestClient(create_app(_settings(), client, forced_user="tester@example.com"))
    missing = http.get(f"/api/documents/{_DOC}/text")
    huge = http.get(f"/api/images/{_IMAGE}")
    bad = http.get("/api/documents/not-a-uuid/text")
    assert missing.status_code == 404
    assert huge.status_code == 413
    assert bad.status_code == 400


def test_static_css_is_public_and_linked() -> None:
    """Styles load without a session. The API and the page stay behind login."""
    http = TestClient(create_app(_settings(), FakeClient({})))
    css = http.get("/static/styles.css", follow_redirects=False)
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert "--paper" in css.text
    assert "--ink" in css.text
    assert "--accent" in css.text
    script = http.get("/static/app.js", follow_redirects=False)
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    page = http.get("/", follow_redirects=False)
    assert page.status_code == 302
    assert page.headers["location"] == "/auth/login"
    signed_in = TestClient(
        create_app(_settings(), FakeClient({}), forced_user="tester@example.com")
    )
    html = signed_in.get("/")
    assert 'href="/static/styles.css' in html.text
    assert "Připraveno" in html.text
    assert html.text.index("Připraveno") < html.text.index('id="answer"')
    assert ".trace-line.mcp" in css.text
    denied = http.post("/api/answer", json={"query": "Vršovice"})
    assert denied.status_code == 401


def test_page_image_id_is_proxied_for_a_signed_in_user() -> None:
    """Full-page ids use the same image proxy. Anonymous callers stay out."""
    page_id = f"page:{_DOC}:1"
    client = FakeClient(
        {},
        images={page_id: ImageBytes(_PNG, "image/jpeg", None)},
    )
    signed_in = TestClient(
        create_app(_settings(), client, forced_user="tester@example.com")
    )
    picture = signed_in.get(f"/api/images/{page_id}")
    assert picture.status_code == 200
    assert picture.content == _PNG
    assert client.calls[-1] == ("get_image", {"image_id": page_id})
    anonymous = TestClient(create_app(_settings(), client))
    assert anonymous.get(f"/api/images/{page_id}").status_code == 401
    assert signed_in.get("/api/images/page:not-a-doc:1").status_code == 400
    page = signed_in.get("/")
    assert 'id="page-lightbox"' in page.text
    script = signed_in.get("/static/app.js").text
    assert 'loading = "lazy"' in script
    assert "page-hit" in script
