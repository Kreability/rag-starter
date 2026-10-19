"""Unfold admin for RAG entities — structured sidebar and grouped fields."""

from __future__ import annotations

from django.contrib import admin, messages
from django.db import transaction
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin, TabularInline

from rag.ingest import delete_document
from rag.models import (
    AuditLog,
    Chunk,
    Conversation,
    Document,
    EvaluationReport,
    EvaluationRun,
    IngestionReport,
    Message,
    QualityScore,
    Status,
    UsageRecord,
)
from rag.tasks import ingest_document_task, run_evaluation_task


@admin.register(UsageRecord)
class UsageRecordAdmin(ModelAdmin):
    list_display = [
        "organization",
        "operation",
        "model",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "created_at",
    ]
    list_filter = ["operation", "model", "created_at"]
    search_fields = ["organization__name", "model"]
    list_per_page = 50
    date_hierarchy = "created_at"
    autocomplete_fields = ["organization"]
    readonly_fields = [
        "organization",
        "operation",
        "model",
        "prompt_tokens",
        "completion_tokens",
        "created_at",
    ]

    @admin.display(description=_("Total tokens"))
    def total_tokens(self, obj):
        return obj.prompt_tokens + obj.completion_tokens


# ── inlines ──────────────────────────────────────────────────────────────────


class ChunkInline(TabularInline):
    model = Chunk
    extra = 0
    can_delete = False
    fields = ["content_type", "page", "content"]
    readonly_fields = fields
    max_num = 0
    show_change_link = True


class MessageInline(TabularInline):
    model = Message
    extra = 0
    fields = ["role", "content", "finish_reason", "created_at"]
    readonly_fields = fields
    can_delete = False
    show_change_link = True


# ── Document ─────────────────────────────────────────────────────────────────


@admin.register(Document)
class DocumentAdmin(ModelAdmin):
    list_display = [
        "name",
        "organization",
        "uploaded_by",
        "is_private",
        "source_type",
        "status_badge",
        "chunk_count",
        "quality_badge",
        "created_at",
    ]
    list_display_links = ["name", "organization"]
    list_filter = ["status", "source_type", "created_at"]
    search_fields = ["name", "source_uri", "organization__name", "uploaded_by__username"]
    list_per_page = 25
    date_hierarchy = "created_at"
    autocomplete_fields = ["organization", "uploaded_by"]
    inlines = [ChunkInline]
    actions = ["reindex_documents", "delete_selected"]

    fieldsets = (
        (_("Source"), {
            "fields": ["name", "organization", "uploaded_by", "is_private", "source_type", "source_uri", "source_options"],
        }),
        (_("Storage"), {
            "fields": ["storage_key", "content_type", "size_bytes", "checksum"],
        }),
        (_("Status"), {
            "fields": ["status", "error_message", "chunk_count", "task_id", "last_ingestion_report"],
        }),
        (_("Timestamps"), {
            "fields": ["created_at", "modified_at"],
        }),
    )
    readonly_fields = [
        "storage_key",
        "checksum",
        "chunk_count",
        "task_id",
        "created_at",
        "modified_at",
        "last_ingestion_report",
    ]

    @admin.display(description=_("Status"), ordering="status")
    def status_badge(self, obj):
        color = {
            Status.UPLOADING: "orange",
            Status.PROCESSING: "blue",
            Status.ENRICHING: "purple",
            Status.READY: "green",
            Status.ERROR: "red",
        }.get(obj.status, "gray")
        return f'<span class="badge" style="background:{color}">{obj.get_status_display()}</span>'

    @admin.display(description=_("Quality"), ordering="last_ingestion_report__quality_score")
    def quality_badge(self, obj):
        report = obj.last_ingestion_report
        if not report:
            return "-"
        color = {
            QualityScore.GOOD: "green",
            QualityScore.WARNING: "orange",
            QualityScore.BAD: "red",
        }.get(report.quality_score, "gray")
        return f'<span class="badge" style="background:{color}">{report.get_quality_score_display()}</span>'

    @admin.action(description=_("Re-run ingestion for selected documents"))
    def reindex_documents(self, request, queryset):
        for document in queryset:
            document.status = Status.PROCESSING
            document.error_message = ""
            document.save(update_fields=["status", "error_message", "modified_at"])
            ingest_document_task.delay(str(document.id))
        self.message_user(
            request, _("Queued %(count)d document(s).") % {"count": queryset.count()},
            messages.SUCCESS,
        )

    def delete_model(self, request, obj):
        delete_document(obj)

    def delete_queryset(self, request, queryset):
        for document in queryset:
            delete_document(document)


# ── Chunk ────────────────────────────────────────────────────────────────────


@admin.register(Chunk)
class ChunkAdmin(ModelAdmin):
    list_display = ["document", "content_type", "page", "content_preview", "created_at"]
    list_display_links = ["document"]
    list_filter = ["content_type", "created_at"]
    search_fields = ["content", "document__name"]
    list_per_page = 50
    autocomplete_fields = ["document"]
    readonly_fields = ["id", "document", "content_hash", "metadata", "created_at"]

    @admin.display(description=_("Preview"))
    def content_preview(self, obj):
        return (obj.content[:120] + "...") if len(obj.content) > 120 else obj.content


# ── Ingestion Report ─────────────────────────────────────────────────────────


@admin.register(IngestionReport)
class IngestionReportAdmin(ModelAdmin):
    list_display = [
        "id",
        "document",
        "quality_badge",
        "pages_detected",
        "pages_with_text",
        "text_chunks",
        "summary_chunks",
        "failed_summaries",
        "created_at",
    ]
    list_display_links = ["id", "document"]
    list_filter = ["quality_score", "created_at"]
    search_fields = ["document__name", "document__organization__name"]
    list_per_page = 25
    date_hierarchy = "created_at"
    readonly_fields = [
        "id",
        "document",
        "pages_detected",
        "pages_with_text",
        "pages_without_text",
        "total_extracted_chars",
        "avg_chars_per_page",
        "extractor_used",
        "ocr_used",
        "ocr_language",
        "text_chunks",
        "table_chunks",
        "image_chunks",
        "summary_chunks",
        "failed_summaries",
        "embedding_status",
        "vector_upload_status",
        "warnings",
        "quality_score",
        "ingestion_duration_seconds",
        "created_at",
    ]

    fieldsets = (
        (_("Document"), {"fields": ["document"]}),
        (_("Pages"), {
            "fields": ["pages_detected", "pages_with_text", "pages_without_text", "avg_chars_per_page"],
        }),
        (_("Extraction"), {
            "fields": ["extractor_used", "ocr_used", "ocr_language", "total_extracted_chars"],
        }),
        (_("Chunks"), {
            "fields": ["text_chunks", "table_chunks", "image_chunks", "summary_chunks", "failed_summaries"],
        }),
        (_("Pipeline"), {
            "fields": ["embedding_status", "vector_upload_status", "ingestion_duration_seconds"],
        }),
        (_("Diagnostics"), {
            "fields": ["warnings", "quality_score", "created_at"],
        }),
    )

    @admin.display(description=_("Quality"), ordering="quality_score")
    def quality_badge(self, obj):
        color = {
            QualityScore.GOOD: "green",
            QualityScore.WARNING: "orange",
            QualityScore.BAD: "red",
        }.get(obj.quality_score, "gray")
        return f'<span class="badge" style="background:{color}">{obj.get_quality_score_display()}</span>'


# ── Conversation ─────────────────────────────────────────────────────────────


@admin.register(Conversation)
class ConversationAdmin(ModelAdmin):
    list_display = ["id", "organization", "title", "message_count", "created_at", "modified_at"]
    list_display_links = ["id", "title"]
    search_fields = ["title", "organization__name"]
    list_per_page = 25
    date_hierarchy = "created_at"
    autocomplete_fields = ["organization"]
    inlines = [MessageInline]
    readonly_fields = ["id", "created_at", "modified_at"]

    @admin.display(description=_("Messages"))
    def message_count(self, obj):
        return obj.messages.count()


# ── Message ──────────────────────────────────────────────────────────────────


@admin.register(Message)
class MessageAdmin(ModelAdmin):
    list_display = ["id", "conversation", "role", "content_preview", "finish_reason", "created_at"]
    list_display_links = ["id", "conversation"]
    list_filter = ["role", "created_at"]
    search_fields = ["content", "conversation__title"]
    list_per_page = 50
    autocomplete_fields = ["conversation"]
    readonly_fields = ["id", "conversation", "role", "content", "citations", "finish_reason", "created_at"]

    @admin.display(description=_("Preview"))
    def content_preview(self, obj):
        return (obj.content[:120] + "...") if len(obj.content) > 120 else obj.content


# ── Audit Log ────────────────────────────────────────────────────────────────


@admin.register(AuditLog)
class AuditLogAdmin(ModelAdmin):
    list_display = ["id", "actor", "action_badge", "resource_type", "resource_id", "ip_address", "created_at"]
    list_display_links = ["id"]
    list_filter = ["action", "created_at"]
    search_fields = ["actor__username", "resource_type", "resource_id"]
    list_per_page = 50
    date_hierarchy = "created_at"
    readonly_fields = [
        "id", "actor", "action", "resource_type", "resource_id",
        "metadata", "ip_address", "created_at",
    ]

    @admin.display(description=_("Action"), ordering="action")
    def action_badge(self, obj):
        color = {
            AuditLog.Action.DOCUMENT_UPLOAD: "blue",
            AuditLog.Action.DOCUMENT_REINDEX: "orange",
            AuditLog.Action.DOCUMENT_DELETE: "red",
            AuditLog.Action.QUERY: "gray",
            AuditLog.Action.ADMIN_ACTION: "purple",
        }.get(obj.action, "gray")
        return f'<span class="badge" style="background:{color}">{obj.get_action_display()}</span>'


# ── Evaluation Report ────────────────────────────────────────────────────────


@admin.register(EvaluationReport)
class EvaluationReportAdmin(ModelAdmin):
    list_display = [
        "id",
        "document",
        "faithfulness_score",
        "answer_relevancy_score",
        "context_precision_score",
        "context_recall_score",
        "chunks_retrieved",
        "created_at",
    ]
    list_display_links = ["id", "document"]
    list_filter = ["document", "created_at"]
    search_fields = ["document__name", "query"]
    list_per_page = 25
    date_hierarchy = "created_at"
    autocomplete_fields = ["document"]
    readonly_fields = [
        "id",
        "document",
        "query",
        "answer",
        "faithfulness_score",
        "answer_relevancy_score",
        "context_precision_score",
        "context_recall_score",
        "chunks_retrieved",
        "evaluation_duration_seconds",
        "metadata",
        "created_at",
    ]

    fieldsets = (
        (_("Document"), {"fields": ["document"]}),
        (_("Query & Answer"), {"fields": ["query", "answer"]}),
        (_("Scores"), {
            "fields": [
                "faithfulness_score",
                "answer_relevancy_score",
                "context_precision_score",
                "context_recall_score",
            ],
        }),
        (_("Metadata"), {
            "fields": ["chunks_retrieved", "evaluation_duration_seconds", "metadata", "created_at"],
        }),
    )


# ── Evaluation runs ──────────────────────────────────────────────────────────


@admin.register(EvaluationRun)
class EvaluationRunAdmin(ModelAdmin):
    """Launch the golden set from the admin: "Add evaluation run" queues it.

    The run executes in the Celery worker; reload the page to watch the status
    and per-case results fill in. Finished runs are read-only history.
    """

    list_display = ["created_at", "organization", "status", "result", "mean_faithfulness", "hit_rate", "use_ragas"]
    list_filter = ["status", "organization", "created_at"]
    list_per_page = 25
    date_hierarchy = "created_at"
    actions = ["rerun"]

    @admin.display(description=_("Passed"))
    def result(self, obj):
        return f"{obj.passed_cases}/{obj.total_cases}" if obj.total_cases else "-"

    @admin.display(description=_("Faithfulness"))
    def mean_faithfulness(self, obj):
        value = (obj.summary or {}).get("mean_faithfulness")
        return f"{value:.2f}" if value is not None else "-"

    @admin.display(description=_("Retrieval hit rate"))
    def hit_rate(self, obj):
        value = (obj.summary or {}).get("retrieval_hit_rate")
        return f"{value:.2f}" if value is not None else "-"

    @admin.display(description=_("Case results"))
    def case_table(self, obj):
        if not obj.results:
            return _("No results yet. Reload this page in a moment.")
        rows = format_html_join(
            "",
            "<tr><td>{}</td><td>{}</td><td>{}s</td><td>{}</td><td>{}</td></tr>",
            (
                (
                    r["id"],
                    "PASS" if r["passed"] else "FAIL",
                    r["seconds"],
                    ", ".join(f"{k[:5]}={v:.2f}" for k, v in r["scores"].items() if v is not None) or "-",
                    "; ".join(r["failures"]) or "-",
                )
                for r in obj.results
            ),
        )
        return format_html(
            "<table><thead><tr><th>Case</th><th>Result</th><th>Time</th><th>Scores</th><th>Failures</th></tr></thead>"
            "<tbody>{}</tbody></table>",
            rows,
        )

    def get_fields(self, request, obj=None):
        if obj is None:
            return ["organization", "use_ragas"]
        return ["organization", "use_ragas", "status", "result", "mean_faithfulness", "hit_rate",
                "error", "triggered_by", "created_at", "finished_at", "case_table"]

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return []
        return self.get_fields(request, obj)

    def has_change_permission(self, request, obj=None):
        # Adding launches a run; existing runs are history, viewable but not editable.
        return obj is None or request.user.is_superuser

    def save_model(self, request, obj, form, change):
        if change:
            return
        obj.triggered_by = request.user
        obj.status = EvaluationRun.State.QUEUED
        super().save_model(request, obj, form, change)
        transaction.on_commit(lambda: run_evaluation_task.delay(str(obj.pk)))
        messages.info(request, _("Evaluation queued. Reload this page to see progress."))

    @admin.action(description=_("Run again (new run with the same settings)"))
    def rerun(self, request, queryset):
        for run in queryset:
            new = EvaluationRun.objects.create(
                organization=run.organization, use_ragas=run.use_ragas, triggered_by=request.user
            )
            transaction.on_commit(lambda pk=new.pk: run_evaluation_task.delay(str(pk)))
        self.message_user(request, _("%d evaluation run(s) queued.") % queryset.count(), messages.SUCCESS)
