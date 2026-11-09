"""Answer pipeline with MCP and Novita replaced by fakes."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from rag_frontend.answer.classify import classify_facts
from rag_frontend.answer.expand import entity_pattern, expand_query, prague_scoped
from rag_frontend.answer.parsers import Fact, facts_from_passage
from rag_frontend.answer.prompt import template_answer
from rag_frontend.api.routes import create_app
from rag_frontend.config import LLM_KEY_ENV, Settings, load_settings
from rag_frontend.mcp_client.session import ImageBytes


class FakeClient:
    """Records tool calls and returns canned text."""

    def __init__(self, tools: dict[str, str]) -> None:
        """Store canned tool bodies."""
        self.tools = tools
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Return the canned body for ``name``."""
        self.calls.append((name, arguments))
        return self.tools.get(name, "No documents stored.")

    async def fetch_image(self, image_id: str) -> ImageBytes:
        """Unused by the answer route."""
        return ImageBytes(None, None, f"Image not found: {image_id}")


def _settings(**kwargs: object) -> Settings:
    """Settings that never leave the test process."""
    base = dict(
        mcp_server_url="http://127.0.0.1:9/mcp/",
        mcp_api_token="mcp-secret",
        host="127.0.0.1",
        port=8000,
        llm_api_key="llm-secret",
        answer_use_llm=True,
        session_secret="test-session",
        public_base_url="http://127.0.0.1:8000",
    )
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


_HINTS = """## Nájemní domy čp. 486, Žitomírská, Praha 10-Vršovice
Adresa: Žitomírská 9–13, Praha 10-Vršovice
Souřadnice: 50.068522, 14.4555164
Confidence: 0.45
Poznámka: fasáda je hypotéza
Zdroje:
- https://example.test/svoboda
"""


def test_expand_vrsovice_uses_alias_not_a_model() -> None:
    """Vršovice grows historical and neighbour variants without a chat call."""
    variants = expand_query("co ví server o lokalitě Vršovice a secesi zde")
    assert variants[0] == "Vršovice"
    assert "Wrschowitz" in variants
    assert any("secesi" in item for item in variants)
    assert prague_scoped("co ví server o lokalitě Vršovice a secesi zde")


def test_building_hint_and_homonym() -> None:
    """A Prague tenement stays; a non-Prague obec id is a homonym."""
    building = facts_from_passage(
        filename="nove-stavby-building-hints.txt",
        doc_id="11111111-1111-1111-1111-111111111111",
        page=None,
        image_id=None,
        content=_HINTS,
    )
    obec = facts_from_passage(
        filename="obce.json",
        doc_id="22222222-2222-2222-2222-222222222222",
        page=None,
        image_id=None,
        content="name: Vršovice\nid: CZ0805123456\nnationalCode: 1",
    )
    relevant, noise, _ranking = classify_facts(
        building + obec,
        entity=entity_pattern("Vršovice secese"),
        prague=True,
    )
    assert relevant[0].confidence == 0.45
    assert relevant[0].gps == (50.068522, 14.4555164)
    assert relevant[0].note == "obrázek nemá"
    assert noise[0].label == "homonym"
    assert noise[0].obec_id.startswith("CZ08")


def test_answer_skips_llm_without_relevant_hits(monkeypatch: object) -> None:
    """Noise-only retrieval does not call Novita."""
    calls: list[str] = []

    class Boom:
        def __init__(self, settings: Settings) -> None:
            del settings

        async def complete(self, user_json: str) -> object:
            calls.append(user_json)
            raise AssertionError("LLM must not be called")

    monkeypatch.setattr("rag_frontend.answer.service.LlmClient", Boom)  # type: ignore[attr-defined]
    client = FakeClient(
        {
            "list_documents": "[]",
            "search_domain_knowledge": (
                "Source: katalog.pdf (source_type=pdf_chunk) "
                "doc_id=33333333-3333-3333-3333-333333333333\n"
                "Content: reklama z Pardubické výstavy 1903\n"
            ),
        }
    )
    http = TestClient(create_app(_settings(), client, forced_user="tester@example.com"))
    response = http.post(
        "/api/answer",
        json={"query": "Vršovice secese", "use_llm": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["objects"] == []
    assert body["debug"]["llm_called"] is False
    assert calls == []
    assert "llm-secret" not in response.text
    assert "mcp-secret" not in response.text
    steps = [item["step"] for item in body["debug"]["trace"]]
    assert "query" in steps
    assert "expand" in steps
    assert "mcp" in steps
    assert "llm" in steps
    joined = json.dumps(body["debug"]["trace"])
    assert "Bearer" not in joined


def test_answer_stream_is_signed_in_only() -> None:
    """The live trace is not available without a session."""
    http = TestClient(create_app(_settings(), FakeClient({})))
    response = http.post(
        "/api/answer/stream",
        json={"query": "Vršovice"},
    )
    assert response.status_code == 401


def test_template_is_prose_not_a_hit_list() -> None:
    """The no-model answer is paragraphs, including the limits section."""
    fact = Fact(
        kind="building",
        title="Nájemní domy čp. 486",
        text="hypotéza",
        doc_id="11111111-1111-1111-1111-111111111111",
        filename="nove-stavby-building-hints.txt",
        address="Žitomírská 9–13, Praha 10-Vršovice",
        confidence=0.45,
        gps=(50.068522, 14.4555164),
        gps_kind="point",
        note="obrázek nemá",
    )
    noise = Fact(
        kind="obec",
        title="Vršovice",
        text="jiná obec",
        doc_id="22222222-2222-2222-2222-222222222222",
        filename="obce.json",
        label="homonym",
        note="jiná obec stejného jména",
    )
    text = template_answer("Vršovice a secese", [fact], [noise])
    assert text.startswith("**K dotazu")
    assert "\n- " not in text
    assert "Co server neví / omezení" in text
    assert "Žitomírská" in text
    assert "[1]" in text


def test_page_is_answer_first() -> None:
    """The main form asks for an answer and does not list the catalog."""
    http = TestClient(
        create_app(_settings(), FakeClient({}), forced_user="tester@example.com")
    )
    page = http.get("/")
    assert page.status_code == 200
    assert "Co o tom ví server" in page.text
    assert 'id="catalog"' not in page.text
    assert "/api/search" not in page.text


def test_anonymous_catalog_does_not_call_mcp() -> None:
    """A missing session is 401 and never reaches the MCP client."""
    client = FakeClient({})
    http = TestClient(create_app(_settings(), client))
    response = http.get("/api/catalog")
    assert response.status_code == 401
    assert client.calls == []
    health = http.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}


def test_llm_key_env_name(monkeypatch: object) -> None:
    """The Gemma key is read from the hyphenated Novita variable."""
    monkeypatch.setenv(LLM_KEY_ENV, "sealed-value")  # type: ignore[attr-defined]
    monkeypatch.setenv("MCP_API_TOKEN", "")  # type: ignore[attr-defined]
    settings = load_settings()
    assert settings.llm_api_key == "sealed-value"
    assert LLM_KEY_ENV == "NOVITA_LLM_API_KEY_GEMMA-3-27"
    assert "NOVIKO" not in LLM_KEY_ENV
