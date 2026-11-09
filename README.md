# fastmcp-rag-timer-frontend

One-page web UI that queries [fastmcp-rag-timer](https://github.com/mkrejp/fastmcp-rag-timer) through a server-side MCP client in this process. The browser talks only to this app. This app holds `MCP_API_TOKEN`, calls Streamable HTTP MCP at `MCP_SERVER_URL` (default `https://rag-timer-00001-production.up.railway.app/mcp/`), and may call Novita chat. Embeddings and Qdrant stay on the RAG server.

Public URL: https://fastmcp-rag-timer-frontend-production.up.railway.app

Agent rules for this repo: [CursingStandards](https://github.com/mkrejp/CursingStandards).

## Documentation

- [docs/INDEX.md](docs/INDEX.md) — topic index
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — current-state architecture
- [docs/CONNECTIVITY.md](docs/CONNECTIVITY.md) — trust boundaries and connection inventory
- [docs/PLAN-rag-answer.md](docs/PLAN-rag-answer.md) — design note (not a description of the running code)

## Run

Copy `.env.example` for local names. The chat key is `NOVITA_LLM_API_KEY_GEMMA-3-27` (hyphens are part of the name; set it in Railway, not with shell `export`). HTML and `/api/*` require a Google session on `GOOGLE_ALLOWED_EMAILS`. `/health` and `/static/*` do not. Without a session the app does not call MCP or Novita. Railway's health check must be `GET /health`.

```text
uv sync
uv run python -m rag_frontend
```
