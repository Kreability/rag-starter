"""Celery tasks.

Ingestion is slow (extraction, LLM summaries, embeddings) so it never runs in
the request cycle. The worker is the same Django image with a different command
— one codebase, not a second service.
"""

from __future__ import annotations

import logging

from celery import shared_task

from rag.ingest import ingest_document
from rag.models import Document, Status

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="rag.ingest_document",
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=3,
    acks_late=True,
    # A worker killed mid-ingest must not silently drop the document.
    reject_on_worker_lost=True,
)
def ingest_document_task(self, document_id: str) -> int:
    """Run the ingestion pipeline for one document."""
    logger.info("Starting ingestion task for %s.", document_id)
    return ingest_document(document_id)


@shared_task(name="rag.reap_stuck_documents")
def reap_stuck_documents(max_age_minutes: int = 60) -> int:
    """Flag documents stuck in PROCESSING past a deadline.

    Covers the case where a worker dies so hard that even `acks_late` cannot
    requeue the job, which would otherwise leave a spinner in the UI forever.
    """
    from datetime import timedelta

    from django.utils import timezone

    cutoff = timezone.now() - timedelta(minutes=max_age_minutes)
    stuck = Document.objects.filter(
        status__in=[Status.PROCESSING, Status.UPLOADING], modified_at__lt=cutoff
    )
    count = stuck.update(
        status=Status.ERROR,
        error_message="Ingestion timed out. Please retry.",
    )
    if count:
        logger.warning("Reaped %d stuck document(s).", count)
    return count
