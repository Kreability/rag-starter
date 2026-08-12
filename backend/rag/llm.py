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
def get_embedder() -> OpenAIEmbeddings:
    """Dense embedder. `dimensions` must match the Qdrant collection."""
    settings = get_config().embedder
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
    settings = get_config().langfuse
    if not settings.enabled:
        return None
    try:
        from langfuse import Langfuse

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
    """
    if _langfuse_client() is None:
        return []
    try:
        from langfuse.callback import CallbackHandler

        settings = get_config().langfuse
        return [
            CallbackHandler(
                public_key=settings.public_key.get_secret_value(),
                secret_key=settings.secret_key.get_secret_value(),
                host=settings.host,
                session_id=session_id,
                user_id=user_id,
                tags=tags or [],
            )
        ]
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
