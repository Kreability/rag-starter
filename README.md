# RAG Starter — Production-Ready Retrieval-Augmented Generation with Django and Next.js

**Build a self-hosted RAG chatbot that answers questions from your own documents, with citations.** A complete, open-source Retrieval-Augmented Generation stack: Django REST API, Next.js 16 frontend, Qdrant hybrid vector search, LangChain + LangGraph orchestration, and Langfuse observability — in one `docker compose up`.

No microservices. No vendor lock-in. Works with OpenAI, Azure OpenAI, Ollama, vLLM, OpenRouter, or any OpenAI-compatible endpoint.

---

## Table of contents

- [What is this?](#what-is-this)
- [Features](#features)
- [Architecture](#architecture)
- [Quickstart](#quickstart)
- [Model providers](#model-providers)
- [How document ingestion works](#how-document-ingestion-works)
- [How question answering works](#how-question-answering-works)
- [Configuration](#configuration)
- [Security](#security)
- [Testing](#testing)
- [Project structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)
- [Credits and license](#credits-and-license)

---

## What is this?

This is a **RAG (Retrieval-Augmented Generation) template** you can actually deploy. Upload PDFs, Word documents, spreadsheets, web pages, or Confluence spaces; ask questions in natural language; get answers grounded in that content, each one citing the exact passages it came from.

It is a port of [stackitcloud/rag-template](https://github.com/stackitcloud/rag-template) — its retrieval pipeline, LangGraph chat graph, and prompt templates — restructured from **five FastAPI microservices plus a Vue/Nx frontend** into **one Django backend and one Next.js app**.

### Why consolidate the microservices?

The upstream project splits the RAG pipeline across `rag-backend`, `admin-backend`, `document-extractor`, and `mcp-server`, wired together with two generated OpenAPI clients and a dependency-injection container, deployed via Helm, Kustomize, Terraform, and Tilt.

That architecture makes sense for a large platform team. For most teams shipping a RAG product, it means four services to deploy, two HTTP hops on every document ingest, and generated client code to regenerate whenever a schema changes.

Here, ingestion is a **function call**:

| Upstream (`rag-template`)                           | This project                                        |
| --------------------------------------------------- | --------------------------------------------------- |
| `services/rag-backend` (FastAPI)                    | `backend/rag/graph.py`, `retrieval.py`, `rerank.py` |
| `services/admin-backend` (FastAPI)                  | `backend/rag/views.py`, `ingest.py`, `models.py`    |
| `services/document-extractor` (FastAPI)             | `backend/rag/extract.py`                            |
| `services/mcp-server`                               | removed (not needed for a web product)              |
| `services/frontend` (Vue + Nx, 2 apps)              | `frontend/apps/web` (Next.js App Router)            |
| 4 shared libs + DI container                        | plain Python modules + `rag/conf.py`                |
| 2 generated OpenAPI clients, 2 HTTP hops per ingest | direct function calls                               |
| Redis key-value store for document status           | Postgres table (survives restarts)                  |
| Helm / Kustomize / Terraform / Tilt                 | `docker compose up`                                 |

Qdrant, MinIO, Redis, and Langfuse remain as _infrastructure_ — those are databases and tools, not services you maintain.

---

## Features

### Retrieval quality

- **Hybrid vector search** — dense embeddings + BM25 sparse vectors, fused by Qdrant. Catches both semantic matches ("how often do we rotate keys?") and exact keyword matches (error codes, identifiers, product names) that pure embedding search misses.
- **Per-content-type retrieval budgets** — text, tables, images, and summaries each get their own `k` and similarity threshold. Tables are never split during chunking, because half a table is worse than a long one.
- **Page-summary expansion** — every page is summarised at ingest time and indexed as a `SUMMARY` chunk that points back at its source chunks. When a summary matches, it expands into the underlying passages. This is what makes broad questions ("what does this contract cover?") work when no single chunk holds the answer.
- **Cross-encoder reranking** — FlashRank (ONNX, CPU-only, no GPU required) reorders candidates before they reach the model.
- **Conversational query rewriting** — follow-ups like "and what about backups?" are rewritten into standalone search queries using chat history, so retrieval actually works mid-conversation.

### Documents

- **Formats**: PDF, DOCX, PPTX, XLSX, CSV, TXT, Markdown, HTML, XML, JSON, EPUB, PNG, JPEG
- **Remote sources**: single web pages, entire sitemaps, Confluence spaces
- **Extraction fallback chain**: Docling (OCR + table structure) → MarkItDown → pypdf → plain text
- **Idempotent re-ingestion** — re-uploading replaces rather than duplicating vectors
- **Live status tracking** — `UPLOADING → PROCESSING → READY | ERROR`, with actionable error messages

### Platform

- **Streaming answers** over server-sent events, with citations rendered before the first token
- **Any OpenAI-compatible provider** — one base URL and key
- **Langfuse tracing** — every chain, token count, and cost; optional, disabled by default
- **Async ingestion** via Celery, so slow PDFs never block a request
- **Multi-tenant by construction** — every vector query is filtered by `owner_id`
- **Django admin** (Unfold theme) for documents, chunks, and conversations
- **OpenAPI schema + Swagger UI** generated from the code

---

## Architecture

```
┌─────────────────────────┐         ┌──────────────────────────────────┐
│  Next.js 16 (App Router)│         │  Django 5 + DRF                  │
│                         │  JWT    │                                  │
│  /chat      streaming   │────────▶│  /api/chat/ask/        SSE       │
│  /documents management  │         │  /api/documents/       CRUD      │
└─────────────────────────┘         │  /api/health/          probes    │
                                    └───────────┬──────────────────────┘
                                                │
                     ┌──────────────────────────┼──────────────────────┐
                     ▼                          ▼                      ▼
              ┌─────────────┐           ┌──────────────┐       ┌──────────────┐
              │   Qdrant    │           │  PostgreSQL  │       │    MinIO     │
              │  vectors +  │           │  documents,  │       │  original    │
              │  hybrid idx │           │  chunks, chat│       │  files (S3)  │
              └─────────────┘           └──────────────┘       └──────────────┘
                     ▲                          ▲
                     │                  ┌───────┴────────┐
                     └──────────────────│ Celery worker  │◀── Redis (broker)
                        embed + upsert  │  ingestion     │
                                        └────────────────┘
```

**Stack:** Python 3.13 · Django 5.1 · Django REST Framework · Celery · LangChain · LangGraph · Qdrant · PostgreSQL 17 · Redis 7 · MinIO · Next.js 16 · React 19 · TypeScript · Tailwind CSS · Docker Compose

---

## Quickstart

**Prerequisites:** Docker and Docker Compose. Nothing else — no local Python, Node, or database.

```bash
# 1. Configuration
cp .env.backend.template .env.backend
cp .env.frontend.template .env.frontend

# 2. Generate a Django secret key
openssl rand -base64 48        # paste into SECRET_KEY in .env.backend

# 3. Add your model provider credentials in .env.backend
#    LLM_API_KEY=sk-...
#    EMBEDDER_API_KEY=sk-...

# 4. Start everything
docker compose up
```

Then create the account the frontend uses and set it in `.env.frontend`:

```bash
make superuser        # or: docker compose exec api uv run python manage.py createsuperuser
```

| Service               | URL                                          |
| --------------------- | -------------------------------------------- |
| **Frontend**          | http://localhost:3000                        |
| API docs (Swagger UI) | http://localhost:8000/api/schema/swagger-ui/ |
| Django admin          | http://localhost:8000/admin/                 |
| Health check          | http://localhost:8000/api/health/            |
| Qdrant dashboard      | http://localhost:6333/dashboard              |
| MinIO console         | http://localhost:9001                        |

Upload a document on **Documents**, wait for `READY`, then ask about it on **Chat**.

### Authentication is currently disabled in the UI

There are no login or register pages. The Django API still requires a JWT and still scopes every document to an owner, so the Next.js **server** signs in as a fixed account and caches the token (`apps/web/lib/token.ts`). Set `DEV_USERNAME` and `DEV_PASSWORD` in `.env.frontend` to match the user you created above.

The credentials stay server-side and never reach the browser — but **anyone who can open the app acts as that user, so do not expose this build publicly.** To restore per-user access, reinstate a session check in `app/(rag)/layout.tsx` and point `authHeaders()` in `lib/rag.ts` back at it. The backend needs no changes; its auth and tenant isolation were never removed.

### Stopping the stack

```bash
make down     # stop and remove containers (data volumes kept)
make clean    # also delete all data: Postgres, Qdrant, MinIO, Redis
```

Services are declared `restart: unless-stopped`, so they return automatically when the Docker daemon restarts. **`docker compose stop` does not survive a daemon restart — use `down`** if you want them to stay off.

---

## Model providers

Anything that speaks the OpenAI API works. Set these in `.env.backend`:

| Provider             | `LLM_BASE_URL`                                  | `LLM_MODEL`                 |
| -------------------- | ----------------------------------------------- | --------------------------- |
| OpenAI               | _(leave empty)_                                 | `gpt-4o-mini`               |
| Azure OpenAI         | `https://<resource>.openai.azure.com/openai/v1` | your deployment             |
| Ollama (local, free) | `http://ollama:11434/v1`                        | `llama3.1`                  |
| vLLM / STACKIT       | `https://your-endpoint/v1`                      | your model                  |
| OpenRouter           | `https://openrouter.ai/api/v1`                  | `anthropic/claude-sonnet-4` |

> **`EMBEDDER_DIMENSIONS` must match your embedding model.** `text-embedding-3-small` = 1536, `text-embedding-3-large` = 3072, `nomic-embed-text` = 768. Changing it later requires re-indexing every document, because existing vectors have the old width.

### Running fully offline

```bash
docker compose --profile local-llm up
docker compose exec ollama ollama pull llama3.1
docker compose exec ollama ollama pull nomic-embed-text
```

Then set `LLM_BASE_URL=http://ollama:11434/v1`, `EMBEDDER_BASE_URL=http://ollama:11434/v1`, `EMBEDDER_MODEL=nomic-embed-text`, and `EMBEDDER_DIMENSIONS=768`.

### LLM observability with Langfuse

```bash
docker compose --profile observability up      # Langfuse at http://localhost:3001
```

Set `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` in `.env.backend` to trace every retrieval, prompt, token count, and cost. Leave them empty and tracing is silently disabled — no code changes needed.

---

## How document ingestion works

```
upload → MinIO → Celery task
   → extract    Docling → MarkItDown → pypdf → plain text
   → chunk      recursive splitter; tables kept whole
   → summarise  one LLM call per page, indexed as SUMMARY chunks
   → embed      dense + sparse vectors
   → upsert     Qdrant, keyed by content hash (idempotent)
   → READY
```

Ingestion runs in a Celery worker, never in the request cycle: a large PDF with page summaries can legitimately take minutes. Status is written to Postgres, so it survives restarts, and a scheduled task flags any document stuck in `PROCESSING`.

**Docling** gives the best extraction — OCR, table structure, layout — but pulls in PyTorch (a large image). It is therefore opt-in:

```bash
cd backend && uv sync --extra docling      # then set INGESTION_PREFER_DOCLING=True
```

Without it, MarkItDown handles PDF, Office, and HTML with no heavyweight dependencies.

---

## How question answering works

The LangGraph chat graph, ported node-for-node from upstream:

```
START → determine_language → rephrase → retrieve ──┬──→ generate → END
                                                   └──→ error_node → END
```

1. **determine_language** — detect the question's language so the answer matches it (LLM, with a `langdetect` fallback)
2. **rephrase** — rewrite follow-ups into standalone search queries using chat history
3. **retrieve** — fan out one search per content type concurrently, expand summary hits into their source chunks, deduplicate, prune to `RETRIEVER_TOTAL_K_DOCUMENTS`, rerank to `RERANKER_K_DOCUMENTS`
4. **generate** — answer strictly from retrieved context, or say so when the context does not contain the answer

Retrieval failures degrade gracefully: one failing content type does not sink the query, and a reranker error returns unreranked results rather than nothing.

---

## Configuration

Every setting is an environment variable, documented inline in `.env.backend.template`. The ones you will actually touch:

| Variable                      | Default                  | Purpose                                      |
| ----------------------------- | ------------------------ | -------------------------------------------- |
| `LLM_MODEL`                   | `gpt-4o-mini`            | Chat model                                   |
| `EMBEDDER_MODEL`              | `text-embedding-3-small` | Embedding model                              |
| `EMBEDDER_DIMENSIONS`         | `1536`                   | **Must match the model**                     |
| `VECTOR_DB_RETRIEVAL_MODE`    | `HYBRID`                 | `HYBRID`, `DENSE`, or `SPARSE`               |
| `RETRIEVER_THRESHOLD`         | `0.5`                    | Minimum similarity to retrieve               |
| `RETRIEVER_TOTAL_K_DOCUMENTS` | `10`                     | Candidates before reranking                  |
| `RERANKER_K_DOCUMENTS`        | `5`                      | Chunks sent to the model                     |
| `CHUNKER_MAX_SIZE`            | `1000`                   | Characters per chunk                         |
| `SUMMARIZER_ENABLED`          | `True`                   | Page summaries (costs one LLM call per page) |
| `INGESTION_MAX_FILE_SIZE_MB`  | `50`                     | Upload size limit                            |
| `THROTTLE_CHAT`               | `120/hour`               | Per-user chat rate limit                     |

### Tuning retrieval

- **Answers miss relevant content** → lower `RETRIEVER_THRESHOLD` (try `0.35`), raise `RETRIEVER_K_DOCUMENTS`
- **Answers include irrelevant passages** → raise the threshold, lower `RERANKER_K_DOCUMENTS`
- **Broad questions answered poorly** → keep `SUMMARIZER_ENABLED=True`; that is exactly what it is for
- **Ingestion too slow or expensive** → set `SUMMARIZER_ENABLED=False` (one fewer LLM call per page)
- **Keyword/identifier searches fail** → confirm `VECTOR_DB_RETRIEVAL_MODE=HYBRID` and `SPARSE_EMBEDDER_ENABLED=True`

---

## Security

The upstream template ran single-tenant behind basic auth inside a trusted cluster. Exposing the same endpoints to real users required more:

- **Tenant isolation** — every vector query carries an `owner_id` filter, enforced in the query itself rather than after the fact. A user cannot retrieve another user's chunks; this is covered by an integration test against a live Qdrant.
- **Upload validation** — extension allowlist, size cap, and magic-byte sniffing, so a PNG renamed `.pdf` is rejected. Filenames are sanitised against path traversal (`../../etc/passwd` → `passwd`).
- **SSRF protection** — ingested URLs are DNS-resolved and rejected if they point at private, loopback, link-local, or cloud-metadata addresses (`169.254.169.254`). Every URL discovered inside a sitemap is re-validated before fetching.
- **Prompt-injection guards** — retrieved chunks and chat history are delimited and explicitly marked as untrusted data in the system prompt (upstream's guards, kept verbatim).
- **Rate limiting** — per-scope DRF throttles on chat and document endpoints, because every call costs embedding and token spend.
- **JWT** with rotation and blacklisting; HSTS, secure cookies, and SSL redirect enabled automatically when `DEBUG=0`.
- **XSS-safe rendering** — model output renders through a Markdown component that emits React elements, never `dangerouslySetInnerHTML`. `javascript:` URLs in links are rendered as plain text.
- **Non-root container** in the production Docker stage.

> **Before deploying publicly:** set `DEBUG=0`, use a strong `SECRET_KEY`, restrict `ALLOWED_HOSTS` and `CORS_ALLOWED_ORIGINS`, set `VECTOR_DB_API_KEY` (and uncomment the matching line in `docker-compose.yaml`), replace the default MinIO credentials, and restore per-user authentication.

---

## Testing

```bash
# Unit tests — no services required
cd backend
uv run pytest rag/tests/ api/tests/ -q

# Integration tests against real Qdrant and MinIO
# (auto-skipped when the services are not reachable)
docker compose up -d db redis qdrant minio
DATABASE_HOST=localhost DATABASE_PORT=5433 VECTOR_DB_URL=http://localhost:6333 \
  uv run pytest rag/tests/test_integration.py -v
```

**52 tests**, covering upload validation and filename sanitisation, the SSRF guard (including DNS-rebinding-style bypasses), summary expansion and deduplication, retrieval pruning, cross-tenant isolation against a live vector database, idempotent re-ingestion, and extraction of CSV, plain text, and long tables.

---

## Project structure

```
backend/
  api/                 Django project: settings, JWT auth, health checks, Celery
  rag/
    conf.py            every tunable, read from the environment
    models.py          Document, Chunk, Conversation, Message
    extract.py         Docling / MarkItDown / pypdf / sitemap / Confluence
    chunking.py        splitting and metadata assembly
    enhance.py         page summarisation
    ingest.py          the ingestion pipeline
    vectordb.py        Qdrant hybrid vector store
    retrieval.py       composite retriever
    rerank.py          FlashRank cross-encoder
    graph.py           LangGraph chat graph + streaming
    prompts.py         prompt templates
    security.py        upload validation + SSRF guard
    views.py           REST API
    tests/             unit + integration tests
frontend/apps/web/
  app/(rag)/           chat and documents pages, SSE proxy route
  components/          chat panel, citations, markdown renderer, document manager
  lib/                 API client, server-side token handling
```

---

## Troubleshooting

**Documents fail with "Could not reach the model provider"**
No API key, or the wrong base URL. Check `LLM_API_KEY` / `EMBEDDER_API_KEY` in `.env.backend`, run `docker compose restart api worker`, then press **Re-index**.

**Chat returns "The knowledge base is empty"**
No document has reached `READY`. Check the Documents page for errors.

**Chat finds nothing despite READY documents**
Usually a similarity threshold set too high for your embedding model, or an `EMBEDDER_DIMENSIONS` mismatch. Lower `RETRIEVER_THRESHOLD` and confirm the dimensions match the model.

**"Port is already allocated" on startup**
Something else on your machine holds that port. Every host port is overridable in a root `.env` file: `API_HOST_PORT`, `WEB_HOST_PORT`, `DATABASE_HOST_PORT`, `QDRANT_HOST_PORT`, `MINIO_HOST_PORT`, `OLLAMA_HOST_PORT`. Find the culprit with `lsof -nP -iTCP:8000 -sTCP:LISTEN`. These only affect access from your machine; containers always talk over the internal Docker network.

**Qdrant will not start after changing its image version**
Qdrant storage is **not forward compatible**. Upgrading across minor versions requires a snapshot restore or a wiped volume (`make clean`). The image and `qdrant-client` are pinned together deliberately — change both at once.

**Health check reports `"status": "degraded"`**
`GET /api/health/` names the failing dependency (`database`, `cache`, `vector_db`, `object_storage`). Run `make init-rag` to create the Qdrant collection and S3 bucket if those are the ones failing.

---

## FAQ

**Do I need a GPU?**
No. Embeddings and generation are API calls, and FlashRank reranking runs on CPU via ONNX. A GPU only matters if you self-host models with Ollama or vLLM.

**Can I use this without OpenAI?**
Yes. Any OpenAI-compatible endpoint works, including fully local Ollama. No code changes — only environment variables.

**Which vector database does this use, and can I swap it?**
Qdrant, for hybrid dense + sparse search, payload filtering, and HNSW indexing. All Qdrant access is isolated in `backend/rag/vectordb.py`, so swapping it means rewriting one module.

**How large a document collection does this handle?**
Qdrant handles millions of vectors on a single node. The practical limits here are ingestion throughput (bounded by your embedding provider's rate limits) and the Celery worker count.

**Does it support multiple users?**
The backend does — every document has an owner and every vector query filters on it. The frontend login UI is currently removed; see [Authentication is currently disabled in the UI](#authentication-is-currently-disabled-in-the-ui).

**How do I add a new file format?**
Add the extension to `ALLOWED_EXTENSIONS` in `backend/rag/security.py` and, if it needs special handling, a branch in `backend/rag/extract.py`.

**Is this production ready?**
The retrieval pipeline, security controls, and infrastructure are. Before going live you still need to restore per-user authentication, harden the deployment settings listed under [Security](#security), and run the production Docker stage (gunicorn, non-root) rather than the development one.

---

## Credits and license

Ported from [stackitcloud/rag-template](https://github.com/stackitcloud/rag-template) (Apache 2.0) — the retrieval pipeline, LangGraph chat graph, and prompt templates originate there.

Built on the [unfoldadmin/turbo](https://github.com/unfoldadmin/turbo) Django + Next.js boilerplate (MIT), which provides the monorepo layout, JWT setup, and Unfold admin theme.

See `LICENSE.md`.

---

<sub>**Keywords:** RAG, Retrieval-Augmented Generation, Django RAG, Next.js RAG, self-hosted RAG, open-source RAG template, LangChain, LangGraph, Qdrant, hybrid search, vector database, semantic search, document Q&A, chat with your documents, PDF chatbot, RAG boilerplate, RAG starter kit, Celery, Langfuse, LLM observability, OpenAI, Ollama, self-hosted AI, enterprise search.</sub>
