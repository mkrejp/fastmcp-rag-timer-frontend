"""Page thumbnails for a new RAG server and the previous one."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi.testclient import TestClient

from rag_frontend.answer.pages import (
    attach_page_previews,
    page_image_id_from_zero_based,
    parse_highlights,
    parse_page_image_id,
)
from rag_frontend.answer.parsers import Fact
from rag_frontend.answer.prompt import user_payload
from rag_frontend.answer.service import configure_answer_cache
from rag_frontend.answer.trace import Trace
from rag_frontend.api.routes import create_app
from rag_frontend.config import Settings

_DOC = "11111111-1111-1111-1111-111111111111"
_PAGE = f"page:{_DOC}:1"


class FakeClient:
    """Canned MCP tools, including an optional missing highlight tool."""

    def __init__(self, tools: dict[str, str]) -> None:
        """Store canned bodies."""
        self.tools = tools
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Return the canned body. A dict is selected by doc_id."""
        self.calls.append((name, arguments))
        body = self.tools.get(name, "[]")
        if isinstance(body, dict):
            return str(body.get(str(arguments.get("doc_id") or ""), "[]"))
        return body


def _settings() -> Settings:
    """Settings that stay inside the test."""
    return Settings(
        mcp_server_url="http://127.0.0.1:9/mcp/",
        mcp_api_token="mcp-secret",
        host="127.0.0.1",
        port=8000,
        llm_api_key="llm-secret",
        answer_use_llm=True,
        session_secret="test-session",
        public_base_url="http://127.0.0.1:8000",
    )


def _fact(page_image_id: str = _PAGE, doc_id: str = _DOC) -> Fact:
    """One relevant PDF passage."""
    return Fact(
        kind="passage",
        title="report.pdf",
        text="Vršovice",
        doc_id=doc_id,
        filename="report.pdf",
        page="0",
        page_image_id=page_image_id,
    )


def test_page_id_uses_one_based_number() -> None:
    """Qdrant page 0 becomes page image 1, and that 1 is not incremented."""
    assert page_image_id_from_zero_based(_DOC, "0") == _PAGE
    assert parse_page_image_id(_PAGE) == (_DOC, 1)
    assert parse_page_image_id("page:not-a-uuid:1") is None


def test_parse_highlights_keeps_unit_boxes() -> None:
    """Boxes outside 0–1 are dropped."""
    text = json.dumps(
        {
            "highlights": [
                {"x0": 0.1, "y0": 0.2, "x1": 0.4, "y1": 0.5, "text": "Vršovice"},
                {"x0": -0.1, "y0": 0.2, "x1": 0.4, "y1": 0.5},
            ]
        }
    )
    assert parse_highlights(text) == [{"x0": 0.1, "y0": 0.2, "x1": 0.4, "y1": 0.5}]
    assert parse_highlights("Error executing get_page_highlights: missing") == []


def test_highlights_are_cached_and_capped() -> None:
    """The second read of one page does not call MCP, and six pages stay at five."""
    configure_answer_cache(600)
    boxes = json.dumps({"highlights": [{"x0": 0.1, "y0": 0.2, "x1": 0.3, "y1": 0.4}]})
    client = FakeClient({"get_page_highlights": boxes})
    fact = _fact()

    async def run() -> None:
        await attach_page_previews(client, "Vršovice", [fact], Trace())
        await attach_page_previews(client, "Vršovice", [_fact()], Trace())

    asyncio.run(run())
    highlight_calls = [
        call for call in client.calls if call[0] == "get_page_highlights"
    ]
    assert len(highlight_calls) == 1
    assert highlight_calls[0][1]["page"] == 1
    assert fact.highlights[0]["x0"] == 0.1
    sent = user_payload("Vršovice", ["Vršovice"], [fact], [])
    assert "highlights" not in sent
    assert "page_image_id" not in sent
    extra = [_fact(f"page:{_DOC}:{index}") for index in range(1, 7)]
    asyncio.run(attach_page_previews(client, "jiný dotaz", extra, Trace()))
    assert sum(1 for item in extra if item.page_image_id) == 5


def test_missing_tool_is_remembered() -> None:
    """An unknown highlight tool disables later calls and drops the preview."""
    configure_answer_cache(600)
    client = FakeClient(
        {"get_page_highlights": "Error Unknown tool: get_page_highlights"}
    )
    first = _fact()

    async def run() -> None:
        await attach_page_previews(client, "první", [first], Trace())
        assert first.page_image_id == ""
        await attach_page_previews(client, "druhý", [_fact()], Trace())

    asyncio.run(run())
    assert [call[0] for call in client.calls] == ["get_page_highlights"]


def test_old_server_answer_does_not_call_page_tools() -> None:
    """A text hit without page_image_id stays a text row."""
    configure_answer_cache(600)
    client = FakeClient(_tools(page_image=False, pdf=False))
    http = TestClient(create_app(_settings(), client, forced_user="tester@example.com"))
    response = http.post("/api/answer", json={"query": "Vršovice", "use_llm": False})
    assert response.status_code == 200
    body = response.json()
    assert body["objects"][0]["page_image_id"] == ""
    assert body["objects"][0]["highlights"] == []
    assert "get_page_highlights" not in {name for name, _args in client.calls}
    assert "get_page_image" not in {name for name, _args in client.calls}


def test_pdf_without_page_image_token_uses_zero_based_page() -> None:
    """A PDF hit with only page_number=0 still previews page 1."""
    configure_answer_cache(600)
    client = FakeClient(_tools(page_image=False, pdf=True))
    http = TestClient(
        create_app(_settings(), client, forced_user="tester@example.com")
    )
    response = http.post(
        "/api/answer",
        json={"query": "Vršovice fallback", "use_llm": False},
    )
    assert response.status_code == 200
    source = response.json()["objects"][0]
    assert source["page_image_id"] == _PAGE
    assert source["page_one"] == 1
    assert source["page"] == "0"
    calls = [args for name, args in client.calls if name == "get_page_highlights"]
    assert calls[0]["page"] == 1


def test_new_server_answer_attaches_highlights_for_page_one() -> None:
    """A 0-based hit with page_image_id asks highlights for page 1 and traces it."""
    configure_answer_cache(600)
    client = FakeClient(_tools(page_image=True))
    http = TestClient(create_app(_settings(), client, forced_user="tester@example.com"))
    response = http.post(
        "/api/answer",
        json={"query": "Vršovice náhled", "use_llm": False},
    )
    assert response.status_code == 200
    body = response.json()
    source = body["objects"][0]
    assert source["page_image_id"] == _PAGE
    assert source["page_one"] == 1
    assert source["page"] == "0"
    assert source["highlights"] == [{"x0": 0.2, "y0": 0.3, "x1": 0.6, "y1": 0.45}]
    highlight_calls = [
        args for name, args in client.calls if name == "get_page_highlights"
    ]
    assert highlight_calls[0] == {"doc_id": _DOC, "page": 1, "query": "Vršovice náhled"}
    steps = [item["step"] for item in body["debug"]["trace"]]
    assert "page_image" in steps
    assert "highlights" in steps
    highlight = next(
        item for item in body["debug"]["trace"] if item["step"] == "highlights"
    )
    assert "duration_ms" in highlight
    assert highlight["page"] == 1
    joined = json.dumps(body)
    assert "llm-secret" not in joined
    assert "mcp-secret" not in joined


def test_stream_emits_the_tool_before_it_returns() -> None:
    """The first SSE frame is immediate, and the end line waits for the call."""
    configure_answer_cache(600)

    class Slow(FakeClient):
        async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
            self.calls.append((name, arguments))
            if name == "search_domain_knowledge":
                await asyncio.sleep(0.35)
            body = self.tools.get(name, "[]")
            if isinstance(body, dict):
                return str(body.get(str(arguments.get("doc_id") or ""), "[]"))
            return body

    client = Slow(_tools(page_image=False, pdf=False))
    http = TestClient(create_app(_settings(), client, forced_user="tester@example.com"))
    started = time.perf_counter()
    seen: list[tuple[float, str, str, str]] = []
    with http.stream(
        "POST",
        "/api/answer/stream",
        json={"query": "Vršovice", "use_llm": False},
    ) as response:
        assert response.status_code == 200
        assert response.headers["x-accel-buffering"] == "no"
        assert "no-cache" in response.headers["cache-control"]
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            seen.append(
                (
                    time.perf_counter() - started,
                    str(event.get("step") or ""),
                    str(event.get("phase") or ""),
                    str(event.get("tool") or ""),
                )
            )
            if event.get("step") == "mcp" and event.get("phase") == "end":
                if event.get("tool") == "search_domain_knowledge":
                    break
    assert seen[0][0] < 0.2
    assert seen[0][1] == "status"
    search_start = next(
        item
        for item in seen
        if item[1:] == ("mcp", "start", "search_domain_knowledge")
    )
    search_end = next(
        item
        for item in seen
        if item[1:] == ("mcp", "end", "search_domain_knowledge")
    )
    assert search_start[0] < search_end[0]
    assert search_end[0] - search_start[0] >= 0.25


def _tools(*, page_image: bool, pdf: bool = True) -> dict[str, str]:
    """Catalog and one hit, with or without the page image field."""
    if pdf:
        header = (
            "Source: report.pdf (source_type=pdf_chunk) "
            f"doc_id={_DOC} page_number=0"
        )
        filename = "report.pdf"
        mime = "application/pdf"
    else:
        header = f"Source: hints.txt (source_type=text_chunk) doc_id={_DOC}"
        filename = "hints.txt"
        mime = "text/plain"
    if page_image:
        header += f" page_image_id={_PAGE}"
    search = f"{header}\nContent: Vršovice na stránce\n"
    return {
        "list_documents": json.dumps(
            [
                {
                    "doc_id": _DOC,
                    "filename": filename,
                    "mime": mime,
                    "uploaded_at": "2020-01-01",
                    "chunk_count": 2,
                    "status": "ready",
                }
            ]
        ),
        "search_domain_knowledge": search,
        "search_images": "No relevant images found.",
        "get_document_text": "Vršovice na stránce, celý text",
        "get_document_images": "[]",
        "get_page_highlights": json.dumps(
            {"highlights": [{"x0": 0.2, "y0": 0.3, "x1": 0.6, "y1": 0.45}]}
        ),
    }
