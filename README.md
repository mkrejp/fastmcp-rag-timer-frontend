# fastmcp-rag-timer-frontend
Frontend web application for fastmcp-rag-timer

look into repo CursingStandards for rules of AI agents processing this repo

Architecture: Web frontend <-> MCP client <-> MCP Server <-> UPSTREAM

Public URL: https://fastmcp-rag-timer-frontend-production.up.railway.app

See [docs/INDEX.md](docs/INDEX.md). The cited-answer plan is [docs/PLAN-rag-answer.md](docs/PLAN-rag-answer.md).

Copy `.env.example` for local names. The chat key is `NOVITA_LLM_API_KEY_GEMMA-3-27` (hyphens are part of the name; set it in Railway, not with shell `export`). Without a Google session the app does not call MCP or Novita. Railway's health check must be `GET /health`.

