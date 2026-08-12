"""Cross-encoder reranking with FlashRank.

Ported from `rag_core_api/impl/reranking/flashrank_reranker.py`, including the
metadata restoration workaround — LangChain's FlashRank wrapper drops metadata,
and citations are worthless without it.

FlashRank is ONNX/CPU, so it adds no GPU requirement.
"""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache

from langchain_core.documents import Document

from rag.conf import get_config

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_ranker():
    from flashrank import Ranker

    settings = get_config().reranker
    return Ranker(model_name=settings.model, cache_dir="/tmp/flashrank")


async def rerank(documents: list[Document], query: str) -> list[Document]:
    """Rerank by cross-encoder relevance and keep the top `k_documents`."""
    if not documents:
        return []
    # FlashRank is synchronous CPU work; keep it off the event loop.
    return await asyncio.to_thread(_rerank_sync, documents, query)


def _rerank_sync(documents: list[Document], query: str) -> list[Document]:
    from flashrank import RerankRequest

    settings = get_config().reranker
    passages = [
        {"id": index, "text": document.page_content}
        for index, document in enumerate(documents)
    ]
    ranked = _get_ranker().rerank(RerankRequest(query=query, passages=passages))

    results: list[Document] = []
    for entry in ranked:
        score = float(entry.get("score", 0.0))
        if score < settings.min_relevance_score:
            continue
        original = documents[int(entry["id"])]
        # Restore the metadata FlashRank drops, then attach the new score.
        original.metadata = {**original.metadata, "relevance_score": score}
        results.append(original)
        if len(results) >= settings.k_documents:
            break

    logger.debug("Reranked %d candidates down to %d.", len(documents), len(results))
    return results
