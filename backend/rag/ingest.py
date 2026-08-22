"""The ingestion pipeline.

Replaces the upstream admin-backend -> document-extractor -> rag-backend
round-trip (three services, two HTTP hops, two generated OpenAPI clients) with
one in-process function:

    fetch -> extract -> caption images -> chunk -> summarise -> embed+upsert -> mark READY

Re-ingesting a source deletes its previous chunks first, so ingestion is
idempotent and never leaves orphaned vectors behind.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from pathlib import Path

from django.db import transaction

from rag import storage, vectordb
from rag.chunking import pieces_to_documents
from rag.enhance import add_summaries
from rag.extract import (
    Piece,
    extract_confluence,
    extract_file,
    extract_sitemap,
    extract_url,
)
from rag.image_captioner import caption_images
from rag.llm import flush_traces, get_trace_callbacks
from rag.metrics import get_metrics, log_context
from rag.models import Chunk, Document, IngestionReport, SourceType, Status
from rag.quality import IngestionDiagnostics, build_warnings, score_quality

logger = logging.getLogger(__name__)


class IngestionError(RuntimeError):
    """Raised when a document cannot be ingested."""


def ingest_document(document_id: str) -> int:
    """Run the pipeline for one document. Returns the number of chunks indexed.

    Always leaves the document in a terminal state (READY or ERROR) — a stuck
    PROCESSING row is worse than a visible failure.
    """
    start = time.perf_counter()
    document = Document.objects.get(pk=document_id)
    metrics = get_metrics()
    metrics.record_ingestion_start(str(document.id), document.source_type)

    document.status = Status.PROCESSING
    document.error_message = ""
    document.save(update_fields=["status", "error_message", "modified_at"])

    callbacks = get_trace_callbacks(
        user_id=str(document.owner_id), tags=["ingest", document.source_type]
    )

    try:
        pieces, extract_diagnostics = _extract(document)
        if not pieces:
            raise IngestionError("No readable content could be extracted from this source.")

        pieces = asyncio.run(caption_images(pieces, document_id=str(document.id)))

        documents = pieces_to_documents(
            pieces,
            document_id=str(document.id),
            owner_id=document.owner_id,
            document_name=document.name,
            storage_key=document.storage_key,
            document_url=document.source_uri,
        )
        if not documents:
            raise IngestionError("Extraction produced no indexable chunks.")

        summaries = asyncio.run(add_summaries(documents, callbacks=callbacks))
        all_documents = documents + summaries

        _purge_existing(document)
        vectordb.upload(all_documents)
        _persist_chunks(document, all_documents)

        image_count = sum(1 for d in all_documents if d.metadata.get("type") == "IMAGE")
        latency = time.perf_counter() - start

        # Build ingestion quality report
        extract_diagnostics.text_chunks = sum(1 for d in documents if d.metadata.get("type") == "TEXT")
        extract_diagnostics.table_chunks = sum(1 for d in documents if d.metadata.get("type") == "TABLE")
        extract_diagnostics.image_chunks = sum(1 for d in documents if d.metadata.get("type") == "IMAGE")
        extract_diagnostics.summary_chunks = len(summaries)
        extract_diagnostics.embedding_status = "completed"
        extract_diagnostics.vector_upload_status = "completed"
        extract_diagnostics.ingestion_duration_seconds = latency
        extract_diagnostics.warnings = build_warnings(extract_diagnostics)
        quality_score = score_quality(extract_diagnostics)

        report = IngestionReport.objects.create(
            document=document,
            pages_detected=extract_diagnostics.pages_detected,
            pages_with_text=extract_diagnostics.pages_with_text,
            pages_without_text=extract_diagnostics.pages_without_text,
            total_extracted_chars=extract_diagnostics.total_extracted_chars,
            avg_chars_per_page=extract_diagnostics.avg_chars_per_page,
            extractor_used=extract_diagnostics.extractor_used,
            ocr_used=extract_diagnostics.ocr_used,
            ocr_language=extract_diagnostics.ocr_language,
            text_chunks=extract_diagnostics.text_chunks,
            table_chunks=extract_diagnostics.table_chunks,
            image_chunks=extract_diagnostics.image_chunks,
            summary_chunks=extract_diagnostics.summary_chunks,
            failed_summaries=extract_diagnostics.failed_summaries,
            embedding_status=extract_diagnostics.embedding_status,
            vector_upload_status=extract_diagnostics.vector_upload_status,
            warnings=extract_diagnostics.warnings,
            quality_score=quality_score,
            ingestion_duration_seconds=latency,
        )
        try:
            document.last_ingestion_report = report
        except ValueError:
            pass
        document.chunk_count = len(all_documents)
        document.status = Status.READY
        document.save(update_fields=["chunk_count", "status", "modified_at"])

        metrics.record_ingestion_success(
            document_id=str(document.id),
            chunks=len(all_documents),
            summaries=len(summaries),
            images=image_count,
            latency=latency,
        )
        logger.info(
            "Ingested '%s': %d chunks (%d summaries, %d images) in %.2fs. Quality=%s",
            document.name,
            len(all_documents),
            len(summaries),
            image_count,
            latency,
            quality_score,
        )
        return len(all_documents)

    except Exception as exc:
        latency = time.perf_counter() - start
        metrics.record_ingestion_failure(str(document.id), str(exc)[:500], latency)
        logger.exception("Ingestion failed for document %s.", document_id)
        document.status = Status.ERROR
        document.error_message = _explain(exc)[:2000]
        document.save(update_fields=["status", "error_message", "modified_at"])
        raise
    finally:
        flush_traces()


def _explain(exc: Exception) -> str:
    """Turn opaque provider errors into something a user can act on."""
    from rag.conf import get_config

    name = type(exc).__name__
    text = str(exc)

    if name in ("APIConnectionError", "OpenAIError") and "onnection" in text.lower():
        base_url = get_config().embedder.base_url or get_config().llm.base_url or "the OpenAI API"
        return (
            f"Could not reach the model provider at {base_url}. "
            "Check LLM_API_KEY / EMBEDDER_API_KEY and the *_BASE_URL settings "
            "in .env.backend, then re-index."
        )
    if name in ("AuthenticationError", "PermissionDeniedError"):
        return "The model provider rejected the API key. Check LLM_API_KEY / EMBEDDER_API_KEY."
    if name == "RateLimitError":
        return "The model provider rate-limited this request. Re-index in a few minutes."
    if name == "NotFoundError":
        return f"The configured model was not found by the provider: {text}"
    if "timeout" in text.lower():
        return "A request timed out. Check network connectivity and provider status, then retry."
    return text or name


def _extract(document: Document) -> tuple[list[Piece], IngestionDiagnostics]:
    """Dispatch to the right extractor for the source type."""
    if document.source_type == SourceType.FILE:
        if not document.storage_key:
            raise IngestionError("Document has no stored file.")
        suffix = Path(document.name).suffix or ".bin"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as handle:
            storage.download_to_path(document.storage_key, handle.name)
            return extract_file(Path(handle.name), document.name)

    if document.source_type == SourceType.URL:
        return extract_url(document.source_uri), IngestionDiagnostics(
            extractor_used="url_fetcher", pages_detected=1
        )

    if document.source_type == SourceType.SITEMAP:
        return extract_sitemap(document.source_uri), IngestionDiagnostics(
            extractor_used="sitemap", pages_detected=1
        )

    if document.source_type == SourceType.CONFLUENCE:
        import os

        options = document.source_options or {}
        space_key = options.get("space_key")
        if not space_key:
            raise IngestionError("Confluence sources require a 'space_key'.")
        token = options.get("token") or os.environ.get("CONFLUENCE_TOKEN", "")
        if not token:
            raise IngestionError("Confluence sources require CONFLUENCE_TOKEN to be set.")
        return extract_confluence(
            url=document.source_uri,
            space_key=space_key,
            token=token,
            verify_ssl=options.get("verify_ssl", True),
        ), IngestionDiagnostics(extractor_used="confluence", pages_detected=1)

    raise IngestionError(f"Unsupported source type '{document.source_type}'.")


def _purge_existing(document: Document) -> None:
    """Remove a document's prior vectors and chunk rows."""
    try:
        vectordb.delete_by(document_id=str(document.id))
    except Exception:
        logger.warning("Could not purge old vectors for %s.", document.id, exc_info=True)
    Chunk.objects.filter(document=document).delete()


@transaction.atomic
def _persist_chunks(document: Document, documents: list) -> None:
    """Mirror chunks into Postgres for the admin UI and future re-indexing."""
    Chunk.objects.bulk_create(
        [
            Chunk(
                document=document,
                content_hash=item.metadata["id"],
                content=item.page_content,
                content_type=item.metadata.get("type", "TEXT"),
                page=str(item.metadata.get("page", ""))[:255],
                metadata={
                    key: value
                    for key, value in item.metadata.items()
                    if key not in ("base64_image",)
                },
            )
            for item in documents
        ],
        batch_size=500,
        ignore_conflicts=True,
    )


def delete_document(document: Document) -> None:
    """Delete a document everywhere: vectors, object storage, database."""
    try:
        vectordb.delete_by(document_id=str(document.id))
    except Exception:
        logger.warning("Could not delete vectors for %s.", document.id, exc_info=True)
    if document.storage_key:
        storage.delete(document.storage_key)
    document.delete()
