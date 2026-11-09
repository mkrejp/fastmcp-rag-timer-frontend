"""Answer pipeline with MCP and Novita replaced by fakes."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from rag_frontend.answer.classify import classify_facts
from rag_frontend.answer.expand import (
    entity_pattern,
    expand_query,
    region_prefixes,
    strip_diacritics,
)
from rag_frontend.answer.parsers import Fact, facts_from_passage, parse_building_hints
from rag_frontend.answer.prompt import template_answer, user_payload
from rag_frontend.answer.retrieve import reverify_facts, select_fulltext_rows
from rag_frontend.answer.service import configure_answer_cache
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
        """Return the canned body for ``name``.

        A dict body is keyed by ``doc_id`` so one tool can serve several files.
        """
        self.calls.append((name, arguments))
        body = self.tools.get(name, "No documents stored.")
        if isinstance(body, dict):
            key = str(arguments.get("doc_id") or "")
            return str(body.get(key, body.get("*", "No documents stored.")))
        return body

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


def _reset_caches() -> None:
    """Drop process-wide catalog, fulltext, and answer caches between tests."""
    configure_answer_cache(600)


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
    assert "Nusle" not in variants
    assert any(strip_diacritics("Vršovice") == item for item in variants)
    assert "Brünn" in expand_query("Brno secese")
    assert "Wienzeile" in expand_query("Vídeň")
    assert region_prefixes("Praha Vršovice") == ("CZ010",)
    assert region_prefixes("Brno") == ("CZ064",)
    assert region_prefixes("Vršovice a secese") == ()


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
        region_prefixes=("CZ010",),
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
    _reset_caches()
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


def test_fulltext_priority_sections_and_homonyms() -> None:
    """Priority files, section matching, re-verify, and every Czech kraj prefix."""
    pattern = entity_pattern("Vršovice")
    catalog = [
        {"doc_id": f"d{index}", "filename": f"note-{index}.txt", "chunk_count": 3}
        for index in range(8)
    ]
    catalog.extend(
        [
            {
                "doc_id": "extra",
                "filename": "vrsovice-poznamky.txt",
                "chunk_count": 2,
            },
            {
                "doc_id": "hints",
                "filename": "nove-stavby-building-hints.txt",
                "chunk_count": 4,
            },
            {"doc_id": "obce", "filename": "obce.json", "chunk_count": 9},
            {"doc_id": "hit", "filename": "katalog.pdf", "chunk_count": 4},
            {"doc_id": "akon", "filename": "akon_1.jpg", "chunk_count": 5},
            {"doc_id": "empty", "filename": "vrsovice-prazdne.txt", "chunk_count": 0},
        ]
    )
    chosen, extra_ids = select_fulltext_rows(catalog, {"hit"}, pattern)
    ids = [row["doc_id"] for row in chosen]
    assert ids[:3] == ["hints", "obce", "hit"]
    assert extra_ids == {"extra"}
    assert "akon" not in ids
    assert "empty" not in ids
    assert "d0" not in ids
    vector = Fact(
        kind="passage",
        title="katalog.pdf",
        text="podobná fasáda",
        doc_id="hit",
        filename="katalog.pdf",
    )
    checked = reverify_facts([vector], {"hit": "úplně jiný text"}, pattern)
    assert checked[0].note == "jen vektorová podobnost"
    long_note = "hypotéza " + ("x" * 400)
    full = (
        "## Nájemní domy čp. 486, Žitomírská, Praha 10-Vršovice\n"
        "Adresa: Žitomírská 9–13, Praha 10-Vršovice\n"
        f"Poznámka: {long_note}\n"
        "## Nusle čp. 1\n"
        "Adresa: Nuselská 1\n"
        "Poznámka: jiná čtvrť\n"
    )
    kept = parse_building_hints("hints.txt", "hints", full, entity=pattern)
    assert [item.title for item in kept] == [
        "Nájemní domy čp. 486, Žitomírská, Praha 10-Vršovice"
    ]
    prague = facts_from_passage(
        filename="obce.json",
        doc_id="obce",
        page=None,
        image_id=None,
        content="name: Vršovice\nid: CZ0105000001\nnationalCode: 1",
    )[0]
    brno = facts_from_passage(
        filename="obce.json",
        doc_id="obce-2",
        page=None,
        image_id=None,
        content="name: Vršovice\nid: CZ0645000001\nnationalCode: 2",
    )[0]
    unnamed, unnamed_noise, _ranking = classify_facts(
        [prague, brno],
        entity=pattern,
        region_prefixes=(),
    )
    assert unnamed == []
    assert {item.note for item in unnamed_noise} == {"kraj není v dotazu"}
    assert "Kraj není v dotazu" in template_answer("Vršovice", [], unnamed_noise)
    moravia, rest, _ranking = classify_facts(
        [prague, brno],
        entity=pattern,
        region_prefixes=region_prefixes("Jihomoravský kraj, Brno"),
    )
    assert [item.obec_id for item in moravia] == ["CZ0645000001"]
    assert rest[0].note == "jiná obec stejného jména"
    assert region_prefixes("Ostrava") == ("CZ080",)
    assert region_prefixes("Ústecký kraj") == ("CZ042",)
    payload = json.loads(
        user_payload(
            "Vršovice",
            ["Vršovice"],
            kept * 6,
            unnamed_noise,
        )
    )
    assert len(payload["objects"]) == 5
    assert all(len(item["text"]) <= 280 for item in payload["objects"])
    assert payload["images"] == []
    assert "doc_id" not in json.dumps(payload["noise"])


def test_answer_merges_images_and_traces_fulltext(monkeypatch: object) -> None:
    """Image search and document images are merged; Novita sees ids and captions."""
    seen: list[str] = []

    class Capture:
        def __init__(self, settings: Settings) -> None:
            del settings

        async def complete(self, user_json: str) -> object:
            seen.append(user_json)
            return type(
                "Synthesis",
                (),
                {
                    "text": "Syntéza.",
                    "prompt_tokens": 11,
                    "completion_tokens": 4,
                    "model": "google/gemma-3-27b-it",
                    "calls": 1,
                    "http_status": 200,
                },
            )()

    monkeypatch.setattr("rag_frontend.answer.service.LlmClient", Capture)  # type: ignore[attr-defined]
    _reset_caches()
    hints_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    vector_id = "ffffffff-ffff-ffff-ffff-ffffffffffff"
    extra_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    obce_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    catalog = json.dumps(
        [
            {
                "doc_id": f"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa{index}",
                "filename": f"note-{index}.txt",
                "mime": "text/plain",
                "uploaded_at": "2020-01-01",
                "chunk_count": 3,
                "status": "ready",
            }
            for index in range(8)
        ]
        + [
            {
                "doc_id": extra_id,
                "filename": "vrsovice-poznamky.txt",
                "mime": "text/plain",
                "uploaded_at": "2020-01-01",
                "chunk_count": 2,
                "status": "ready",
            },
            {
                "doc_id": hints_id,
                "filename": "nove-stavby-building-hints.txt",
                "mime": "text/plain",
                "uploaded_at": "2020-01-01",
                "chunk_count": 4,
                "status": "ready",
            },
            {
                "doc_id": obce_id,
                "filename": "obce.json",
                "mime": "application/json",
                "uploaded_at": "2020-01-01",
                "chunk_count": 10,
                "status": "ready",
            },
            {
                "doc_id": vector_id,
                "filename": "katalog.pdf",
                "mime": "application/pdf",
                "uploaded_at": "2020-01-01",
                "chunk_count": 4,
                "status": "ready",
            },
            {
                "doc_id": "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
                "filename": "akon_pohled.jpg",
                "mime": "image/jpeg",
                "uploaded_at": "2020-01-01",
                "chunk_count": 1,
                "status": "ready",
            },
        ]
    )
    full_hints = (
        "## Nájemní domy čp. 486, Žitomírská, Praha 10-Vršovice\n"
        "Adresa: Žitomírská 9–13, Praha 10-Vršovice\n"
        "Poznámka: " + ("fasáda " * 80) + "\n"
        "## Nusle čp. 1\n"
        "Adresa: Nuselská 1\n"
        "Poznámka: jiná čtvrť\n"
    )
    search = (
        "Source: nove-stavby-building-hints.txt (source_type=txt_chunk) "
        f"doc_id={hints_id}\n"
        "Content: ## Nusle čp. 1\nAdresa: Nuselská 1\nPoznámka: jiná čtvrť\n"
        "\n---\n"
        "Source: katalog.pdf (source_type=pdf_chunk) "
        f"doc_id={vector_id}\n"
        "Content: podobná fasáda bez jména\n"
    )
    pictures = (
        "Source: nove-stavby-building-hints.txt (source_type=image) "
        f"doc_id={hints_id} image_id=aabbccddeeff0011\n"
        "Content: fasáda\n"
    )
    client = FakeClient(
        {
            "list_documents": catalog,
            "search_domain_knowledge": search,
            "search_images": pictures,
            "get_document_text": {
                hints_id: full_hints,
                vector_id: "úplně jiný text",
                extra_id: "soubor bez zmínky",
                obce_id: "name: Vršovice\nid: CZ0105000001\nnationalCode: 1",
            },
            "get_document_images": {
                hints_id: json.dumps(
                    [
                        {
                            "image_id": "0011223344556677",
                            "ocr_text": "TAJNY OCR TEXT KTERY NEMUZE DO LLM",
                        }
                    ]
                ),
                "*": "[]",
            },
        }
    )
    http = TestClient(create_app(_settings(), client, forced_user="tester@example.com"))
    response = http.post(
        "/api/answer",
        json={"query": "Vršovice secese Žitomírská ověření", "use_llm": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert [item["title"] for item in body["objects"]] == [
        "Nájemní domy čp. 486, Žitomírská, Praha 10-Vršovice"
    ]
    assert all(len(item["text"]) <= 280 for item in body["objects"])
    assert {item["image_id"] for item in body["images"]} == {
        "aabbccddeeff0011",
        "0011223344556677",
    }
    notes = {item["note"] for item in body["noise"]}
    assert "jen vektorová podobnost" in notes
    assert "0 shod" in notes
    assert "kraj není v dotazu" in notes
    steps = [item["step"] for item in body["debug"]["trace"]]
    assert "fulltext" in steps
    assert "absent_corpora" in steps
    fulltext = [item for item in body["debug"]["trace"] if item["step"] == "fulltext"]
    assert {"filename", "match", "window_chars"} <= fulltext[0].keys()
    absent = next(
        item for item in body["debug"]["trace"] if item["step"] == "absent_corpora"
    )
    assert {"filename": "vrsovice-poznamky.txt", "matches": 0} in absent["files"]
    assert seen
    sent = seen[0]
    assert "TAJNY OCR" not in sent
    assert "aabbccddeeff0011" in sent
    assert "0011223344556677" in sent
    assert "Nuselská" not in sent
    parsed = json.loads(sent)
    assert len(parsed["objects"]) <= 5
    assert all(len(item["text"]) <= 280 for item in parsed["objects"])
    assert {item["image_id"] for item in parsed["images"]} == {
        "aabbccddeeff0011",
        "0011223344556677",
    }


def test_llm_key_env_name(monkeypatch: object) -> None:
    """The Gemma key is read from the hyphenated Novita variable."""
    monkeypatch.setenv(LLM_KEY_ENV, "sealed-value")  # type: ignore[attr-defined]
    monkeypatch.setenv("MCP_API_TOKEN", "")  # type: ignore[attr-defined]
    settings = load_settings()
    assert settings.llm_api_key == "sealed-value"
    assert LLM_KEY_ENV == "NOVITA_LLM_API_KEY_GEMMA-3-27"
    assert "NOVIKO" not in LLM_KEY_ENV
