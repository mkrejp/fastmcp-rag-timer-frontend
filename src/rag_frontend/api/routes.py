"""JSON routes and the one-page query UI."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from rag_frontend.config import Settings
from rag_frontend.mcp_client.parse import (
    parse_catalog,
    parse_images,
    parse_search,
)
from rag_frontend.mcp_client.session import (
    HttpMcpClient,
    ImageBytes,
    McpCallError,
    McpToolClient,
    MissingTokenError,
)
from rag_frontend.redact import RedactTokenMiddleware, install_log_redaction

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
_DOC_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_IMAGE_ID = re.compile(r"^[a-f0-9]{16}$")
_LIMIT_MAX = 20


class SearchRequest(BaseModel):
    """Body for ``POST /api/search``."""

    query: str
    limit: int = 3
    include_images: bool = True


class PassageModel(BaseModel):
    """One retrieved passage, with an image id when the hit is a picture."""

    source: str
    source_type: str
    content: str
    doc_id: str | None = None
    page_number: str | None = None
    image_id: str | None = None
    url: str | None = None


class SearchResponse(BaseModel):
    """Text hits plus optional picture hits."""

    passages: list[PassageModel]
    image_passages: list[PassageModel] = Field(default_factory=list)
    message: str | None = None
    image_message: str | None = None
    raw: str | None = None


class CatalogDocument(BaseModel):
    """Public catalog row."""

    doc_id: str | None = None
    filename: str | None = None
    mime: str | None = None
    uploaded_at: str | None = None
    chunk_count: int | None = None
    status: str | None = None
    thumbnail_image_id: str | None = None


class CatalogResponse(BaseModel):
    """Document catalog for the side panel."""

    documents: list[CatalogDocument]
    message: str | None = None


class ImageMetaModel(BaseModel):
    """Public metadata for one stored picture."""

    image_id: str
    page_number: int | None = None
    width: int | None = None
    height: int | None = None
    mime: str | None = None
    ocr_text: str | None = None
    ocr_confidence: float | None = None


class ImageListResponse(BaseModel):
    """Pictures stored for one document."""

    images: list[ImageMetaModel]
    message: str | None = None


class DocumentTextResponse(BaseModel):
    """Processed full text for one document."""

    doc_id: str
    text: str


def create_app(
    settings: Settings,
    client: McpToolClient | None = None,
) -> FastAPI:
    """Build the query app.

    Query routes refuse to call MCP when ``MCP_API_TOKEN`` is missing. The
    HTML page still loads.
    """
    install_log_redaction(settings.mcp_api_token)
    app = FastAPI(title="RAG query")
    app.state.settings = settings
    app.state.mcp_client = client if client is not None else _default_client(settings)
    app.add_middleware(RedactTokenMiddleware, token=settings.mcp_api_token)

    @app.get("/api/catalog", response_model=CatalogResponse)
    async def catalog(request: Request) -> CatalogResponse:
        """List documents stored in the RAG catalog."""
        tool_client = _require_client(request)
        text = await _call(tool_client, "list_documents", {"since": ""})
        view = parse_catalog(text)
        if view.error:
            raise HTTPException(status_code=502, detail=view.error)
        thumbnails = await asyncio.gather(
            *[_thumbnail_id(tool_client, row.get("doc_id")) for row in view.documents]
        )
        documents = []
        for row, thumbnail_id in zip(view.documents, thumbnails, strict=True):
            row["thumbnail_image_id"] = thumbnail_id
            documents.append(CatalogDocument.model_validate(row))
        return CatalogResponse(documents=documents, message=view.message)

    @app.post("/api/search", response_model=SearchResponse)
    async def search(body: SearchRequest, request: Request) -> SearchResponse:
        """Search stored text and, when asked, stored pictures."""
        query = body.query.strip()
        if not query:
            raise HTTPException(status_code=400, detail="Enter a question.")
        if body.limit < 1 or body.limit > _LIMIT_MAX:
            raise HTTPException(
                status_code=400,
                detail=f"Results must be from 1 to {_LIMIT_MAX}.",
            )
        tool_client = _require_client(request)
        text = await _call(
            tool_client,
            "search_domain_knowledge",
            {"query": query, "limit": body.limit},
        )
        view = parse_search(text)
        if view.error:
            raise HTTPException(status_code=502, detail=view.error)
        image_passages: list[PassageModel] = []
        image_message: str | None = None
        if body.include_images:
            image_passages, image_message = await _image_search(
                tool_client,
                query,
                body.limit,
            )
        return SearchResponse(
            passages=[
                PassageModel.model_validate(item.to_dict()) for item in view.passages
            ],
            image_passages=image_passages,
            message=view.message,
            image_message=image_message,
            raw=view.raw,
        )

    @app.get("/api/documents/{doc_id}/text", response_model=DocumentTextResponse)
    async def document_text(doc_id: str, request: Request) -> DocumentTextResponse:
        """Return processed full text for one document."""
        _check_doc_id(doc_id)
        tool_client = _require_client(request)
        text = await _call(tool_client, "get_document_text", {"doc_id": doc_id})
        _raise_for_document(text)
        return DocumentTextResponse(doc_id=doc_id, text=text)

    @app.get("/api/documents/{doc_id}/images", response_model=ImageListResponse)
    async def document_images(doc_id: str, request: Request) -> ImageListResponse:
        """List pictures stored for one document."""
        _check_doc_id(doc_id)
        tool_client = _require_client(request)
        text = await _call(tool_client, "get_document_images", {"doc_id": doc_id})
        view = parse_images(text)
        if view.error and view.error.startswith("Document not found"):
            raise HTTPException(status_code=404, detail=view.error)
        if view.error:
            raise HTTPException(status_code=502, detail=view.error)
        images = [ImageMetaModel.model_validate(item.to_dict()) for item in view.images]
        return ImageListResponse(images=images, message=view.message)

    @app.get("/api/images/{image_id}")
    async def image(image_id: str, request: Request) -> Response:
        """Proxy one stored picture so the browser never sees the MCP token."""
        _check_image_id(image_id)
        tool_client = _require_client(request)
        try:
            payload = await tool_client.fetch_image(image_id)
        except MissingTokenError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except McpCallError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return _image_response(payload)

    @app.get("/")
    async def index() -> Response:
        """Serve the one-page query UI."""
        page = WEB_DIR / "index.html"
        return Response(
            content=page.read_bytes(), media_type="text/html; charset=utf-8"
        )

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app


def _default_client(settings: Settings) -> HttpMcpClient | None:
    """Build a live client only when a token is configured."""
    if not settings.token_configured:
        return None
    return HttpMcpClient(settings)


async def _thumbnail_id(client: McpToolClient, doc_id: object) -> str | None:
    """Return the first stored picture for a catalog row, if there is one."""
    if not isinstance(doc_id, str) or not doc_id:
        return None
    try:
        text = await client.call_tool("get_document_images", {"doc_id": doc_id})
    except (MissingTokenError, McpCallError):
        return None
    view = parse_images(text)
    if view.error or not view.images:
        return None
    return view.images[0].image_id


def _require_client(request: Request) -> McpToolClient:
    """Return the MCP client or refuse the query route."""
    client = request.app.state.mcp_client
    if client is None:
        raise HTTPException(status_code=503, detail="MCP_API_TOKEN is not set.")
    return client


async def _image_search(
    client: McpToolClient,
    query: str,
    limit: int,
) -> tuple[list[PassageModel], str | None]:
    """Search stored pictures without failing the text results."""
    try:
        image_text = await _call(
            client,
            "search_images",
            {"query": query, "limit": limit, "hybrid": False},
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else "Picture search failed."
        return [], detail
    image_view = parse_search(image_text)
    if image_view.error:
        return [], image_view.error
    passages = [
        PassageModel.model_validate(item.to_dict()) for item in image_view.passages
    ]
    return passages, image_view.message


async def _call(client: McpToolClient, name: str, arguments: dict[str, Any]) -> str:
    """Call one tool and map transport failures to HTTP errors."""
    try:
        return await client.call_tool(name, arguments)
    except MissingTokenError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except McpCallError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _check_doc_id(doc_id: str) -> None:
    """Reject document ids that are not UUIDs from the RAG catalog."""
    if _DOC_ID.fullmatch(doc_id) is None:
        raise HTTPException(status_code=400, detail="Unknown document id.")


def _check_image_id(image_id: str) -> None:
    """Reject image ids that are not the server's 16-hex identifiers."""
    if _IMAGE_ID.fullmatch(image_id) is None:
        raise HTTPException(status_code=400, detail="Unknown image id.")


def _raise_for_document(text: str) -> None:
    """Map document tool errors onto HTTP status codes."""
    stripped = text.strip()
    if stripped.startswith("Document not found"):
        raise HTTPException(status_code=404, detail=stripped)
    if stripped.startswith("Error "):
        raise HTTPException(status_code=502, detail=stripped)


def _image_response(payload: ImageBytes) -> Response:
    """Return image bytes or an HTTP error that explains why they are missing."""
    if payload.data is not None and payload.mime_type:
        return Response(content=payload.data, media_type=payload.mime_type)
    message = payload.message or "Image not found."
    if "exceeds MCP inline limit" in message:
        raise HTTPException(status_code=413, detail=message)
    if message.startswith("Error "):
        raise HTTPException(status_code=502, detail=message)
    raise HTTPException(status_code=404, detail=message)
