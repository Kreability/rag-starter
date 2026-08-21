# Client Requirement Analysis — RAG Starter

Use this to match a client's situation to the right configuration, model choices, and deployment architecture.

---

## 1. Discovery Questions

Ask these first to classify the client:

| Category | Questions | Why it matters |
|----------|-----------|----------------|
| **Data sensitivity** | Is the data PII, PHI, financial, or regulated (GDPR, HIPAA, SOC2)? | Determines if data can leave the network |
| **Volume** | How many documents? How many GB/tokens per month? | Affects embedding costs, worker sizing, Qdrant scale |
| **User count** | How many concurrent users? Internal vs external? | Affects rate limits, Django/Gunicorn workers, infra cost |
| **Latency** | Is real-time streaming required? What's the acceptable response time? | Affects model choice (smaller = faster), infrastructure |
| **Languages** | What languages are the documents and queries in? | Affects embedding model choice and chunking quality |
| **Existing stack** | Do they already use OpenAI, Azure, AWS, GCP, Anthropic? | Reduces integration friction |
| **Budget** | Upfront vs monthly? Infrastructure budget? | Determines self-hosted vs managed, model tier |
| **Offline/air-gap** | Must it run without internet? | Forces self-hosted models |
| **Maintenance** | Do they have DevOps staff? SLA requirements? | Affects managed vs self-hosted decision |
| **Accuracy needs** | Is "good enough" acceptable, or do they need state-of-the-art? | Affects model tier and eval rigor |

---

## 2. Situation-to-Configuration Matrix

### Situation A: Startup / MVP / Proof of Concept

**Profile:**
- Small team, limited budget
- Need to validate the product fast
- Data is not highly sensitive
- Low user count (<50)

**Recommended: Tier 1 (OpenAI)**

```bash
LLM_MODEL=gpt-4o-mini
EMBEDDER_PROVIDER=openai
EMBEDDER_MODEL=text-embedding-3-small
EMBEDDER_DIMENSIONS=1536
```

**Why:**
- Zero infrastructure management
- Pay-per-use, no fixed costs
- Best quality-to-cost ratio
- Fastest time-to-market

**Cost estimate:** ~$50-200/month depending on usage

**Deployment:** Docker Compose, single small VM (2 vCPU, 4GB RAM)

---

### Situation B: Growing SaaS / Productized RAG

**Profile:**
- Paying customers
- Need reliability and speed
- Moderate data volume (10GB-1TB)
- 50-500 users
- SOC2 or GDPR consideration

**Recommended: Tier 1 or Tier 2**

```bash
# If staying OpenAI
LLM_MODEL=gpt-4o-mini
EMBEDDER_MODEL=text-embedding-3-small

# If wanting provider flexibility
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=anthropic/claude-sonnet-4
```

**Why:**
- OpenAI gives predictable quality and SLAs
- OpenRouter gives provider flexibility and fallback options
- Can self-host embeddings if needed for data residency

**Cost estimate:** $500-5,000/month

**Deployment:** Docker Compose or Kubernetes, managed Postgres/Qdrant, CDN for frontend

---

### Situation C: Enterprise / Regulated Industry

**Profile:**
- Healthcare (HIPAA), finance (PCI/SOC2), legal
- Data cannot leave premises or specific cloud region
- High volume (1TB+ documents)
- 500+ users, SSO required
- Audit logs required

**Recommended: Tier 3 (Self-Hosted) or Private Cloud**

```bash
# Chat
LLM_MODEL=llama3.1:70b  # or llama3.1:8b for lighter footprint
LLM_BASE_URL=http://ollama:11434/v1

# Embeddings
EMBEDDER_PROVIDER=local
EMBEDDER_MODEL=nomic-embed-text
EMBEDDER_DIMENSIONS=768

# Observability
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
```

**Why:**
- Data never leaves the network
- Full audit trail via Langfuse (self-hosted)
- Can run on air-gapped hardware

**Cost estimate:** High upfront (GPUs), low recurring. ~$2,000-10,000 hardware + $500/month infra

**Deployment:** Kubernetes or bare metal, GPU nodes for LLM, CPU nodes for API/worker, private networking, VPN/SSO integration

---

### Situation D: High-Volume / Cost-Optimized

**Profile:**
- Millions of queries per month
- Large document corpus (100K+ docs)
- Cost is a primary concern
- Can tolerate slightly lower quality

**Recommended: Tier 3 with smaller models**

```bash
LLM_MODEL=llama3.1:8b
EMBEDDER_MODEL=BAAI/bge-small-en-v1.5
EMBEDDER_DIMENSIONS=384
RERANKER_ENABLED=True
SUMMARIZER_ENABLED=False  # Disable to save LLM calls
```

**Why:**
- Zero per-token costs
- `8b` model is fast enough for bulk queries
- Disabling summarizer cuts ingestion cost ~20-30%

**Trade-off:** Lower answer quality on complex questions

**Cost estimate:** Hardware amortized, ~$500-2,000/month infra

---

### Situation E: Offline / Edge / Air-Gapped

**Profile:**
- No internet access (military, research, field ops)
- Must run on local hardware
- Data cannot be transmitted externally

**Recommended: Tier 3 (Ollama + FastEmbed)**

```bash
LLM_MODEL=llama3.1:8b
LLM_BASE_URL=http://ollama:11434/v1
EMBEDDER_PROVIDER=local
EMBEDDER_MODEL=BAAI/bge-small-en-v1.5
SPARSE_EMBEDDER_ENABLED=True
```

**Why:**
- Everything runs locally
- No API keys or external calls
- Single Docker Compose or binary deployment

**Cost estimate:** Hardware one-time, ~$1,000-3,000 for capable workstation

---

### Situation F: Multilingual / Global

**Profile:**
- Documents in 5+ languages
- Users querying in different languages
- Need cross-lingual retrieval

**Recommended: Tier 1 or Tier 2 with multilingual models**

```bash
# Chat (Claude handles multilingual well)
LLM_MODEL=claude-3-5-sonnet-20240620
LLM_BASE_URL=https://openrouter.ai/api/v1

# Embeddings
EMBEDDER_PROVIDER=openai
EMBEDDER_MODEL=text-embedding-3-large  # 3072 dim, stronger multilingual
EMBEDDER_DIMENSIONS=3072

# Or self-hosted multilingual
EMBEDDER_PROVIDER=local
EMBEDDER_MODEL=nomic-embed-text
EMBEDDER_DIMENSIONS=768
```

**Why:**
- `text-embedding-3-large` has stronger multilingual performance
- Claude/GPT-4o handle multilingual generation well
- `nomic-embed-text` is the best local multilingual option

---

## 3. Architecture by Scale

| Scale | Users | Docs | Qdrant | Postgres | Celery Workers | infra |
|-------|-------|------|--------|----------|----------------|-------|
| **Starter** | 1-10 | <100 | Single node | 1 CPU, 2GB | 1 worker, 2GB | $50-100/month |
| **Business** | 10-100 | 100-10K | Single node | 2 CPU, 4GB | 2 workers, 4GB | $300-1,000/month |
| **Scale** | 100-1000 | 10K-100K | Single node + replicas | 4 CPU, 8GB | 4 workers, 8GB | $1,000-5,000/month |
| **Enterprise** | 1000+ | 100K+ | Cluster | 8+ CPU, 32GB+ | 8+ workers, 16GB+ | $5,000+/month |

---

## 4. Security & Compliance Mapping

| Requirement | Setting / Action |
|-------------|------------------|
| **Data residency** | Use Tier 3 (self-hosted) or Azure OpenAI with region lock |
| **Audit logging** | Enable Langfuse self-hosted, set `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY` |
| **Encryption at rest** | Enable Qdrant encryption, enable Postgres encryption, enable MinIO encryption |
| **Encryption in transit** | Set `DEBUG=0`, use `docker-compose.prod.yml` with Caddy/TLS |
| **SSO / SAML** | Add to frontend (NextAuth supports SAML), keep JWT for API |
| **Rate limiting** | Tune `THROTTLE_CHAT`, `THROTTLE_DOCUMENTS` per user tier |
| **Tenant isolation** | Already enforced at vector query level — verify with integration tests |
| **Retention policies** | Add Celery beat task to archive/delete old conversations |
| **Backup** | Use `make backup` cron, store offsite |
| **Vulnerability scanning** | Enable Dependabot, run `trivy` on Docker images |

---

## 5. Cost Breakdown (Monthly Estimates)

### Tier 1: OpenAI

| Component | Cost | Notes |
|-----------|------|-------|
| OpenAI API | $50-500 | Pay-per-token, scales with usage |
| Infrastructure | $100-300 | Small VM, managed DB/storage |
| **Total** | **$150-800** | |

### Tier 2: OpenRouter / Anthropic

| Component | Cost | Notes |
|-----------|------|-------|
| LLM API | $200-2,000 | Higher per-token, better quality |
| Embeddings | $20-100 | Still OpenAI or similar |
| Infrastructure | $100-300 | Same as Tier 1 |
| **Total** | **$320-2,400** | |

### Tier 3: Self-Hosted

| Component | Cost | Notes |
|-----------|------|-------|
| Hardware | $2,000-10,000 | One-time (GPU server) |
| Infrastructure | $200-1,000 | VM, storage, bandwidth |
| Electricity | $50-200 | GPU power draw |
| **Total** | **$250-1,200/month recurring** + hardware |

---

## 6. Migration Path

Most clients will start at Tier 1 and migrate as needs change. The system is designed for this:

| Migration | What changes | What stays the same |
|-----------|-------------|---------------------|
| **OpenAI → OpenRouter** | `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | Everything else |
| **OpenAI → Ollama** | `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, add `--profile local-llm` | Everything else |
| **text-embedding-3-small → nomic-embed-text** | `EMBEDDER_PROVIDER`, `EMBEDDER_MODEL`, `EMBEDDER_DIMENSIONS` | Chunks, retrieval code |
| **Add reranker** | `RERANKER_ENABLED=True` (already True by default) | Everything else |
| **Add Langfuse** | Set keys in `.env.backend` | Everything else |

**Critical:** Changing `EMBEDDER_DIMENSIONS` requires re-ingesting all documents. Plan for this.

---

## 7. Red Flags — When This Stack Is NOT the Right Fit

| Situation | Why it doesn't fit | Alternative |
|-----------|-------------------|-------------|
| **Real-time voice/video** | RAG is text-in/text-out; latency too high for real-time | Build separate voice pipeline |
| **Millions of small documents** | Qdrant single-node has limits at 100M+ vectors | Use Elasticsearch or dedicated vector DB |
| **Sub-100ms latency requirement** | Retrieval + reranking + generation = 500ms-2s | Use smaller model, disable reranker, cache aggressively |
| **No engineering staff** | Requires Docker, env config, monitoring | Use managed service (Pinecone, Weaviate Cloud, etc.) |
| **Structured data queries** | RAG is for unstructured text | Use SQL/GraphQL |

---

## 8. Recommended Default for Kreability Clients

Based on Kreability's positioning (AI agents, SaaS, production-grade), the recommended default is:

**Tier 1 with optional Tier 2 fallback:**

```bash
# Primary: OpenAI
LLM_MODEL=gpt-4o-mini
EMBEDDER_PROVIDER=openai
EMBEDDER_MODEL=text-embedding-3-small
EMBEDDER_DIMENSIONS=1536

# Observability
LANGFUSE_PUBLIC_KEY=<client-key>
LANGFUSE_SECRET_KEY=<client-secret>

# Production hardening
DEBUG=0
SECRET_KEY=<generated>
VECTOR_DB_API_KEY=<set>
```

**Add-ons by client:**
- **Enterprise:** Self-hosted Ollama + `llama3.1:70b`, Azure OpenAI with private endpoint
- **High-volume:** Disable summarizer, increase `CELERY_CONCURRENCY`, add worker replicas
- **Multilingual:** Switch to `text-embedding-3-large` + Claude/GPT-4o
- **Compliance:** Enable Langfuse self-hosted, disable cloud APIs, add backup cron
