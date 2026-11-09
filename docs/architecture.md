---
id: architecture
summary: Web frontend, MCP client, MCP server, and upstream.
parent: index
children: []
---

# Architecture

The browser asks this app. This app is the MCP client. The MCP server is fastmcp-rag-timer. Embeddings and Qdrant stay upstream of that server.

## overview

```text
Web frontend  ->  MCP client  ->  MCP server  ->  upstream
(this page)       (this process)   /mcp            Qdrant, FastEmbed
```

The page is same-origin JSON plus HTML. It never holds `MCP_API_TOKEN` and never calls `/mcp`. The server has no CORS middleware, and picture URLs on that server require the same bearer token, so this app proxies image bytes.

## inventory

| Key | Value | Notes |
| --- | --- | --- |
| `MCP_SERVER_URL` | `https://rag-timer-00001-production.up.railway.app/mcp/` | Streamable HTTP. Keep the trailing slash. |
| `MCP_API_TOKEN` | unset | Required for query routes. The page still loads without it. |
| `HOST` | `0.0.0.0` | Bind address. Railway must reach every interface. |
| `PORT` | `8000` | Port for this app. Railway sets this to the domain port, `8080`. |
| Public URL | `https://fastmcp-rag-timer-frontend-production.up.railway.app` | Production domain. The browser uses this origin. |
| `search_domain_knowledge` | read | Text hits. A hit may include `image_id`. |
| `search_images` | read | Stored pictures. Optional on each question. |
| `list_documents` | read | Catalog panel. |
| `get_document_text` | read | Opened document text. |
| `get_document_images` | read | Pictures stored on one document. |
| `get_image` | read | Inline bytes, proxied at `/api/images/{image_id}`. |

Ingest, delete, and locality-admin tools are not called.

## procedures

### run — Local page

1. `uv sync`
2. `export MCP_API_TOKEN=…`
3. `uv run python -m rag_frontend`
4. Open `http://127.0.0.1:8000/`. Production is `https://fastmcp-rag-timer-frontend-production.up.railway.app`.

### query — What the page shows

1. The form submits a question. The default result count is 3.
2. The transcript lists text passages and, when the picture checkbox is on, picture hits.
3. A hit with an image id loads `/api/images/{image_id}`.
4. Open document loads full text and the pictures stored for that document.
5. The catalog lists filename, id, time, chunk count, status, and mime. Hashes and disk paths stay on the server.

## references

- [Index](INDEX.md)
- [fastmcp-rag-timer](https://github.com/mkrejp/fastmcp-rag-timer)
