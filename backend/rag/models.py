"""Relational state for the RAG system.

Qdrant owns the vectors; Postgres owns everything you want to query, list,
authorise or audit. Upstream kept document status in a Redis key-value store,
which loses state on restart — a table is both simpler and durable.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class Status(models.TextChoices):
    """Ported from `admin_api_lib/models/status.py`."""

    UPLOADING = "UPLOADING", _("Uploading")
    PROCESSING = "PROCESSING", _("Processing")
    READY = "READY", _("Ready")
    ERROR = "ERROR", _("Error")


class SourceType(models.TextChoices):
    FILE = "FILE", _("File")
    URL = "URL", _("URL")
    SITEMAP = "SITEMAP", _("Sitemap")
    CONFLUENCE = "CONFLUENCE", _("Confluence")


class ContentType(models.TextChoices):
    """Ported from `rag_core_lib/impl/data_types/content_type.py`."""

    TEXT = "TEXT", _("Text")
    TABLE = "TABLE", _("Table")
    IMAGE = "IMAGE", _("Image")
    SUMMARY = "SUMMARY", _("Summary")


class Document(models.Model):
    """One ingested source: an uploaded file, a URL, a sitemap or a Confluence space."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="documents",
        verbose_name=_("owner"),
    )
    name = models.CharField(_("name"), max_length=512)
    source_type = models.CharField(
        _("source type"), max_length=16, choices=SourceType.choices, default=SourceType.FILE
    )
    source_uri = models.TextField(_("source URI"), blank=True, default="")
    status = models.CharField(
        _("status"), max_length=16, choices=Status.choices, default=Status.UPLOADING
    )
    error_message = models.TextField(_("error message"), blank=True, default="")

    # Object storage
    storage_key = models.CharField(_("storage key"), max_length=1024, blank=True, default="")
    content_type = models.CharField(_("MIME type"), max_length=255, blank=True, default="")
    size_bytes = models.BigIntegerField(_("size in bytes"), default=0)
    checksum = models.CharField(_("sha256"), max_length=64, blank=True, default="")

    chunk_count = models.PositiveIntegerField(_("chunk count"), default=0)
    task_id = models.CharField(_("celery task id"), max_length=255, blank=True, default="")
    # Source-specific options, e.g. {"space_key": "ENG"} for Confluence.
    source_options = models.JSONField(_("source options"), default=dict, blank=True)

    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    modified_at = models.DateTimeField(_("modified at"), auto_now=True)

    class Meta:
        db_table = "rag_documents"
        verbose_name = _("document")
        verbose_name_plural = _("documents")
        ordering = ["-created_at"]
        constraints = [
            # A user cannot ingest the same source name twice; re-upload replaces.
            models.UniqueConstraint(fields=["owner", "name"], name="unique_document_per_owner")
        ]
        indexes = [
            models.Index(fields=["owner", "status"]),
            models.Index(fields=["owner", "-created_at"]),
        ]

    def __str__(self) -> str:
        return self.name


class Chunk(models.Model):
    """Mirror of a Qdrant point.

    Keeping the text in Postgres means citations, the admin UI and re-indexing
    never need a vector round-trip, and a Qdrant rebuild is a re-embed away.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="chunks", verbose_name=_("document")
    )
    # sha256 of the content; this is `metadata.id` in Qdrant.
    content_hash = models.CharField(_("content hash"), max_length=64, db_index=True)
    content = models.TextField(_("content"))
    content_type = models.CharField(
        _("content type"), max_length=16, choices=ContentType.choices, default=ContentType.TEXT
    )
    page = models.CharField(_("page"), max_length=255, blank=True, default="")
    metadata = models.JSONField(_("metadata"), default=dict, blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        db_table = "rag_chunks"
        verbose_name = _("chunk")
        verbose_name_plural = _("chunks")
        ordering = ["document", "id"]
        indexes = [models.Index(fields=["document", "content_type"])]

    def __str__(self) -> str:
        return f"{self.document.name} · {self.content_type} · {self.content[:40]}"


class Conversation(models.Model):
    """A chat thread. History is server-side so the client cannot forge context."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversations",
        verbose_name=_("owner"),
    )
    title = models.CharField(_("title"), max_length=255, blank=True, default="")
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    modified_at = models.DateTimeField(_("modified at"), auto_now=True)

    class Meta:
        db_table = "rag_conversations"
        verbose_name = _("conversation")
        verbose_name_plural = _("conversations")
        ordering = ["-modified_at"]
        indexes = [models.Index(fields=["owner", "-modified_at"])]

    def __str__(self) -> str:
        return self.title or str(self.id)


class Message(models.Model):
    class Role(models.TextChoices):
        USER = "user", _("User")
        ASSISTANT = "assistant", _("Assistant")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages", verbose_name=_("conversation")
    )
    role = models.CharField(_("role"), max_length=16, choices=Role.choices)
    content = models.TextField(_("content"))
    citations = models.JSONField(_("citations"), default=list, blank=True)
    finish_reason = models.CharField(_("finish reason"), max_length=255, blank=True, default="")
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        db_table = "rag_messages"
        verbose_name = _("message")
        verbose_name_plural = _("messages")
        ordering = ["created_at"]
        indexes = [models.Index(fields=["conversation", "created_at"])]

    def __str__(self) -> str:
        return f"{self.role}: {self.content[:50]}"
