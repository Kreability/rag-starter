"""Unfold admin for RAG entities."""

from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin, TabularInline

from rag.ingest import delete_document
from rag.models import Chunk, Conversation, Document, Message, Status
from rag.tasks import ingest_document_task


class ChunkInline(TabularInline):
    model = Chunk
    extra = 0
    can_delete = False
    fields = ["content_type", "page", "content"]
    readonly_fields = fields
    # A large document has thousands of chunks; never render them all inline.
    max_num = 0
    show_change_link = True


@admin.register(Document)
class DocumentAdmin(ModelAdmin):
    list_display = ["name", "owner", "source_type", "status", "chunk_count", "created_at"]
    list_filter = ["status", "source_type", "created_at"]
    search_fields = ["name", "source_uri", "owner__username"]
    readonly_fields = [
        "id",
        "storage_key",
        "checksum",
        "chunk_count",
        "task_id",
        "created_at",
        "modified_at",
    ]
    autocomplete_fields = ["owner"]
    inlines = [ChunkInline]
    actions = ["reindex_documents"]

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


@admin.register(Chunk)
class ChunkAdmin(ModelAdmin):
    list_display = ["document", "content_type", "page", "created_at"]
    list_filter = ["content_type"]
    search_fields = ["content", "document__name"]
    readonly_fields = ["id", "document", "content_hash", "metadata", "created_at"]


class MessageInline(TabularInline):
    model = Message
    extra = 0
    fields = ["role", "content", "finish_reason", "created_at"]
    readonly_fields = fields
    can_delete = False


@admin.register(Conversation)
class ConversationAdmin(ModelAdmin):
    list_display = ["__str__", "owner", "created_at", "modified_at"]
    search_fields = ["title", "owner__username"]
    readonly_fields = ["id", "created_at", "modified_at"]
    autocomplete_fields = ["owner"]
    inlines = [MessageInline]
