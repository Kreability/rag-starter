"""Qdrant hybrid vector store.

Ported from `rag_core_api/impl/vector_databases/qdrant_database.py`, with the
upstream `from_documents` re-assignment replaced by an explicit
create-collection-once + `add_documents` flow (the upstream version silently
rebuilt the store on every upload, which is not safe for concurrent ingests).

Chunk metadata contract (kept identical to upstream so ports stay faithful):
    id           str  - sha256 content hash, the point's stable identity
    document_id  str  - owning Document UUID, used for deletes
    owner_id     int  - Django user id, enforced as a hard retrieval filter
    type         str  - ContentType: TEXT | TABLE | IMAGE | SUMMARY
    related      list - ids of sibling chunks (summaries point at their pages)
    document_url str  - pre-signed link back to the original file
    page         any  - page number or page title
"""

from __future__ import annotations

import logging
from functools import lru_cache

from langchain_core.documents import Document
from qdrant_client import QdrantClient
from qdrant_client.http import models
from qdrant_client.models import Distance, VectorParams

from rag.conf import get_config
from rag.llm import get_embedder, get_sparse_embedder

logger = logging.getLogger(__name__)

CONTENT_TYPES = ("TEXT", "TABLE", "IMAGE", "SUMMARY")


@lru_cache(maxsize=1)
def get_client() -> QdrantClient:
    settings = get_config().vector_db
    return QdrantClient(
        url=settings.url,
        api_key=settings.api_key.get_secret_value() or None,
        timeout=int(settings.timeout),
        prefer_grpc=settings.prefer_grpc,
    )


def _retrieval_mode():
    from langchain_qdrant import RetrievalMode

    mode = get_config().vector_db.retrieval_mode.upper()
    if mode == "HYBRID" and get_sparse_embedder() is None:
        logger.warning("HYBRID requested but sparse embedder unavailable; using DENSE.")
        return RetrievalMode.DENSE
    return getattr(RetrievalMode, mode, RetrievalMode.DENSE)


def ensure_collection() -> None:
    """Create the collection and its payload indexes if missing. Idempotent."""
    settings = get_config().vector_db
    client = get_client()
    name = settings.collection_name

    if not client.collection_exists(name):
        vectors_config = {
            "dense": VectorParams(
                size=get_config().embedder.dimensions, distance=Distance.COSINE
            )
        }
        sparse_config = (
            {"sparse": models.SparseVectorParams(index=models.SparseIndexParams())}
            if get_sparse_embedder() is not None
            else None
        )
        client.create_collection(
            collection_name=name,
            vectors_config=vectors_config,
            sparse_vectors_config=sparse_config,
        )
        logger.info("Created Qdrant collection '%s'.", name)

    # Payload indexes make the per-type / per-owner filters cheap instead of a scan.
    for field in ("metadata.type", "metadata.document_id", "metadata.owner_id", "metadata.id"):
        try:
            client.create_payload_index(
                collection_name=name,
                field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
        except Exception:
            # Already exists — Qdrant has no "create if not exists" for indexes.
            logger.debug("Payload index %s already present.", field)


@lru_cache(maxsize=1)
def get_vectorstore():
    """Long-lived QdrantVectorStore bound to the configured collection."""
    from langchain_qdrant import QdrantVectorStore

    ensure_collection()
    settings = get_config().vector_db
    kwargs = {
        "client": get_client(),
        "collection_name": settings.collection_name,
        "embedding": get_embedder(),
        "retrieval_mode": _retrieval_mode(),
        "vector_name": "dense",
    }
    sparse = get_sparse_embedder()
    if sparse is not None:
        kwargs["sparse_embedding"] = sparse
        kwargs["sparse_vector_name"] = "sparse"
    return QdrantVectorStore(**kwargs)


def build_filter(filter_kwargs: dict | None) -> models.Filter | None:
    """Translate a flat dict into a Qdrant `must` filter over `metadata.*`."""
    if not filter_kwargs:
        return None
    return models.Filter(
        must=[
            models.FieldCondition(key=f"metadata.{key}", match=models.MatchValue(value=value))
            for key, value in filter_kwargs.items()
        ]
    )


def collection_available() -> bool:
    """True when the collection exists and holds at least one point."""
    settings = get_config().vector_db
    client = get_client()
    try:
        if not client.collection_exists(settings.collection_name):
            return False
        return client.get_collection(settings.collection_name).points_count > 0
    except Exception:
        logger.exception("Could not determine collection availability.")
        return False


async def asearch(
    query: str, *, k: int, score_threshold: float, filter_kwargs: dict | None = None
) -> list[Document]:
    """Similarity search returning documents with a `score` in metadata."""
    store = get_vectorstore()
    try:
        results = await store.asimilarity_search_with_score(
            query, k=k, filter=build_filter(filter_kwargs)
        )
    except Exception:
        logger.exception("Vector search failed for query=%r filters=%s", query, filter_kwargs)
        raise

    documents: list[Document] = []
    for doc, score in results:
        if score < score_threshold:
            continue
        doc.metadata["score"] = score
        documents.append(doc)
    return documents


def get_documents_by_ids(ids: list[str]) -> list[Document]:
    """Batch-fetch chunks by their `metadata.id`.

    Upstream looped one scroll per id; this issues a single `should` (OR) scroll.
    """
    if not ids:
        return []
    settings = get_config().vector_db
    points, _ = get_client().scroll(
        collection_name=settings.collection_name,
        scroll_filter=models.Filter(
            should=[
                models.FieldCondition(key="metadata.id", match=models.MatchValue(value=i))
                for i in ids
            ]
        ),
        limit=max(len(ids), 1),
        with_payload=True,
    )
    return [
        Document(
            page_content=point.payload.get("page_content", ""),
            metadata=point.payload.get("metadata", {}),
        )
        for point in points
    ]


def upload(documents: list[Document]) -> None:
    """Upsert chunks. Point ids are derived from `metadata.id`, so re-ingesting
    the same content overwrites rather than duplicates."""
    if not documents:
        return
    ensure_collection()
    ids = [_point_id(doc.metadata["id"]) for doc in documents]
    get_vectorstore().add_documents(documents, ids=ids)


def delete_by(**filter_kwargs) -> None:
    """Delete every point matching the given metadata equality filters."""
    settings = get_config().vector_db
    qdrant_filter = build_filter(filter_kwargs)
    if qdrant_filter is None:
        raise ValueError("Refusing to delete without a filter.")
    get_client().delete(
        collection_name=settings.collection_name,
        points_selector=models.FilterSelector(filter=qdrant_filter),
    )


def _point_id(content_hash: str) -> str:
    """Qdrant point ids must be UUIDs or ints; map the sha256 to a stable UUID."""
    import uuid

    return str(uuid.UUID(content_hash[:32]))
