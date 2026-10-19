"""LLM, embedding and tracing factories.

Everything speaks the OpenAI wire protocol, so a single pair of
`*_BASE_URL` / `*_API_KEY` env vars retargets the whole system at OpenAI,
Azure, vLLM, STACKIT, Ollama, OpenRouter, ...

Ported from `rag_core_lib/impl/llms/llm_factory.py`,
`rag_core_lib/impl/embeddings/*` and `rag_core_lib/impl/langfuse_manager/*`.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from langchain_core.embeddings import Embeddings
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from rag.conf import get_config

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_chat_model() -> ChatOpenAI:
    """Chat model used for rephrasing, language detection, answering and summarising."""
    settings = get_config().llm
    return ChatOpenAI(
        model=settings.model,
        api_key=settings.api_key.get_secret_value() or "not-needed",
        base_url=settings.base_url,
        temperature=settings.temperature,
        top_p=settings.top_p,
        max_tokens=settings.max_tokens,
        timeout=settings.timeout,
        max_retries=3,
    )


@lru_cache(maxsize=1)
def get_embedder():
    """Dense embedder.

    Set `EMBEDDER_PROVIDER=local` to embed in-process with FastEmbed (ONNX, CPU,
    no API key). That matters because several chat providers — OpenRouter, Groq,
    DeepSeek — serve no embedding models at all, and RAG needs both.

    Otherwise any OpenAI-compatible embedding endpoint is used.
    """
    settings = get_config().embedder

    if settings.provider.lower() == "local":
        from langchain_community.embeddings.fastembed import FastEmbedEmbeddings

        logger.info("Using local FastEmbed embedder: %s", settings.local_model)
        return FastEmbedEmbeddings(model_name=settings.local_model)

    if settings.provider.lower() == "nvidia":
        # One gateway key for everything: reuse the chat key unless the
        # embedder is given its own.
        api_key = settings.api_key.get_secret_value() or get_config().llm.api_key.get_secret_value()
        return NvidiaEmbeddings(
            model=settings.model,
            api_key=api_key,
            base_url=settings.base_url or "https://integrate.api.nvidia.com/v1",
            batch_size=settings.batch_size,
            timeout=settings.timeout,
        )

    return _openai_embedder(settings)


class NvidiaEmbeddings(Embeddings):
    """NVIDIA NIM retrieval embeddings (e.g. `nvidia/nv-embedqa-e5-v5`).

    The endpoint is OpenAI-compatible, but asymmetric retrieval models require
    `input_type` ("query" for questions, "passage" for documents) and reject
    requests without it, which `OpenAIEmbeddings` cannot send. Using the wrong
    type silently degrades retrieval quality, so queries and documents are
    embedded through separate paths.
    """

    def __init__(self, *, model: str, api_key: str, base_url: str, batch_size: int = 64, timeout: float = 120.0):
        from openai import AsyncOpenAI, OpenAI

        if not api_key:
            raise ValueError("Set LLM_API_KEY (or EMBEDDER_API_KEY) when EMBEDDER_PROVIDER=nvidia.")
        self.model = model
        # NIM caps a request at 50-ish inputs; stay conservative.
        self.batch_size = max(1, min(batch_size, 50))
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=3)
        self._aclient = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=3)

    def _kwargs(self, input_type: str) -> dict:
        return {
            "model": self.model,
            "encoding_format": "float",
            "extra_body": {"input_type": input_type, "truncate": "END"},
        }

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            response = self._client.embeddings.create(input=batch, **self._kwargs("passage"))
            vectors.extend(item.embedding for item in response.data)
        return vectors

    def embed_query(self, text: str) -> list[float]:
        response = self._client.embeddings.create(input=[text], **self._kwargs("query"))
        return response.data[0].embedding

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            response = await self._aclient.embeddings.create(input=batch, **self._kwargs("passage"))
            vectors.extend(item.embedding for item in response.data)
        return vectors

    async def aembed_query(self, text: str) -> list[float]:
        response = await self._aclient.embeddings.create(input=[text], **self._kwargs("query"))
        return response.data[0].embedding


def _openai_embedder(settings) -> OpenAIEmbeddings:
    kwargs = {
        "model": settings.model,
        "api_key": settings.api_key.get_secret_value() or "not-needed",
        "base_url": settings.base_url,
        "chunk_size": settings.batch_size,
        "timeout": settings.timeout,
        "max_retries": 3,
    }
    # Only OpenAI's v3 models accept `dimensions`; self-hosted endpoints reject it.
    if settings.model.startswith("text-embedding-3"):
        kwargs["dimensions"] = settings.dimensions
    return OpenAIEmbeddings(**kwargs)


@lru_cache(maxsize=1)
def get_sparse_embedder():
    """BM25-style sparse vectors for Qdrant hybrid search.

    Returns None when disabled or when fastembed is unavailable, in which case
    the vector store silently falls back to dense-only retrieval.
    """
    settings = get_config().sparse_embedder
    if not settings.enabled:
        return None
    try:
        from langchain_qdrant import FastEmbedSparse

        return FastEmbedSparse(model_name=settings.model)
    except Exception:
        logger.warning(
            "Sparse embedder '%s' unavailable; falling back to dense-only retrieval.",
            settings.model,
            exc_info=True,
        )
        return None


@lru_cache(maxsize=1)
def _langfuse_client():
    """Construct (and cache) the Langfuse client.

    The v3+ SDK is env-var-driven: the global client and its LangChain
    `CallbackHandler` both resolve credentials from `LANGFUSE_PUBLIC_KEY` /
    `LANGFUSE_SECRET_KEY` / `LANGFUSE_BASE_URL`, not constructor kwargs — the
    constructor here only accepts `public_key` as an override. Export the
    settings this app already validated (`LANGFUSE_*` in `.env.backend`) into
    the process environment so both the client and the callback handler pick
    them up consistently.
    """
    settings = get_config().langfuse
    if not settings.enabled:
        return None
    try:
        import os

        from langfuse import Langfuse

        os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.public_key.get_secret_value())
        os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.secret_key.get_secret_value())
        os.environ.setdefault("LANGFUSE_BASE_URL", settings.host)

        return Langfuse(
            public_key=settings.public_key.get_secret_value(),
            secret_key=settings.secret_key.get_secret_value(),
            host=settings.host,
        )
    except Exception:
        logger.warning("Langfuse client init failed; tracing disabled.", exc_info=True)
        return None


def get_trace_callbacks(
    *, session_id: str | None = None, user_id: str | None = None, tags: list[str] | None = None
) -> list:
    """LangChain callbacks that ship traces to Langfuse.

    Returns an empty list when Langfuse is not configured, so every call site
    can pass `callbacks=get_trace_callbacks(...)` unconditionally.

    `session_id`/`user_id`/`tags` are accepted for call-site compatibility but
    not yet attached to the trace: Langfuse's v3 SDK moved this from
    `CallbackHandler(...)` constructor kwargs to a `propagate_attributes(...)`
    context manager wrapping the traced call, which none of this module's
    callers currently use. Traces still ship; they just aren't tagged with
    session/user/tags until a caller adopts that context manager.
    """
    if _langfuse_client() is None:
        return []
    try:
        from langfuse.langchain import CallbackHandler

        # Credentials are read from the environment `_langfuse_client()`
        # exported: the handler's only constructor kwarg (`public_key`) is an
        # override for routing to a non-default project, not full auth.
        return [CallbackHandler()]
    except Exception:
        logger.warning("Langfuse callback creation failed.", exc_info=True)
        return []


def flush_traces() -> None:
    """Flush buffered traces. Celery workers must call this before exiting."""
    client = _langfuse_client()
    if client is not None:
        try:
            client.flush()
        except Exception:
            logger.debug("Langfuse flush failed.", exc_info=True)
