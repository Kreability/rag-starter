"""REST API.

Replaces upstream's `rag_api.py` (chat) and `admin_api.py` (documents) with two
DRF viewsets. Every queryset is filtered by `request.user`: authentication alone
is not authorisation, and documents are per-user data.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import uuid
import zipfile

from asgiref.sync import async_to_sync
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.db.models import Q
from django.http import StreamingHttpResponse
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from api.permissions import IsOrgAdmin
from rag import storage
from rag.audit import log_document_delete, log_document_reindex, log_query
from rag.conf import get_config
from rag.graph import answer as run_answer
from rag.graph import astream_answer
from rag.ingest import delete_document
from rag.llm import get_trace_callbacks
from rag.models import (
    AuditLog,
    Chunk,
    Conversation,
    Document,
    EvaluationReport,
    Message,
    SourceType,
    Status,
)
from rag.security import validate_upload
from rag.serializers import (
    AuditLogSerializer,
    BulkUploadSerializer,
    ChatRequestSerializer,
    ChatResponseSerializer,
    ChunkSerializer,
    ConversationDetailSerializer,
    ConversationSerializer,
    DocumentSerializer,
    DocumentUploadSerializer,
    EvaluationReportSerializer,
    IngestionReportSerializer,
    SourceUploadSerializer,
)
from rag.tasks import ingest_document_task

logger = logging.getLogger(__name__)


def visible_documents(user):
    """Return documents the caller may read within their organization."""
    if not getattr(user, "organization_id", None):
        return Document.objects.none()
    queryset = Document.objects.filter(organization_id=user.organization_id)
    if getattr(user, "is_org_admin", False):
        return queryset
    return queryset.filter(Q(is_private=False) | Q(uploaded_by=user)).distinct()


def can_modify_document(user, document: Document) -> bool:
    """Only the uploader or an org admin may replace, re-index or delete a document."""
    if document.organization_id != getattr(user, "organization_id", None):
        return False
    return bool(getattr(user, "is_org_admin", False) or document.uploaded_by_id == user.pk)


def _wants_replace(request) -> bool:
    return str(request.data.get("replace", "")).lower() in {"1", "true", "yes"}


class DocumentViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Document management: upload, ingest sources, poll status, delete."""

    serializer_class = DocumentSerializer
    permission_classes = [IsAuthenticated]
    # File upload uses multipart; the source/scraper endpoint sends JSON.
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "documents"

    def get_queryset(self):
        return visible_documents(self.request.user)

    @extend_schema(
        request=DocumentUploadSerializer,
        responses={202: DocumentSerializer},
        description="Upload a file and queue it for ingestion.",
    )
    @action(detail=False, methods=["post"], url_path="upload")
    def upload(self, request):
        serializer = DocumentUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        uploaded = serializer.validated_data["file"]
        organization = request.user.organization

        name = uploaded.sanitized_name
        content_type = uploaded.resolved_content_type
        storage_key = f"{request.user.pk}/{uuid.uuid4()}/{name}"

        existing = self._existing(organization, name)
        conflict = self._conflict(request, existing, name)
        if conflict is not None:
            return conflict

        # Store the bytes before creating the row, so a READY row always has a file.
        try:
            storage.upload_fileobj(uploaded, storage_key, content_type)
        except Exception:
            logger.exception("Object storage upload failed for '%s'.", name)
            return Response(
                {"detail": "Could not store the file. Please try again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        document, created = self._save_document(
            existing,
            organization=organization,
            name=name,
            uploader=request.user,
            defaults={
                "is_private": serializer.validated_data.get("is_private", False),
                "source_type": SourceType.FILE,
                "storage_key": storage_key,
                "content_type": content_type,
                "size_bytes": uploaded.size,
            },
        )
        self._queue(document)
        return Response(
            DocumentSerializer(document).data,
            status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
        )

    @extend_schema(
        request=BulkUploadSerializer,
        responses={202: DocumentSerializer(many=True)},
        description="Upload multiple files or a ZIP archive and queue them for ingestion.",
    )
    @action(detail=False, methods=["post"], url_path="bulk-upload")
    def bulk_upload(self, request):
        serializer = BulkUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        organization = request.user.organization

        files_to_upload: list[tuple[str, io.BytesIO, str]] = []

        if data.get("zip_file"):
            zip_error = self._read_zip(data["zip_file"], files_to_upload)
            if zip_error is not None:
                return zip_error

        if data.get("files"):
            for uploaded in data["files"]:
                try:
                    name, content_type = validate_upload(uploaded)
                except DjangoValidationError as exc:
                    return Response(
                        {"detail": exc.messages},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                content = uploaded.read()
                files_to_upload.append((name, io.BytesIO(content), content_type))

        if not files_to_upload:
            return Response(
                {"detail": "No valid files found."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        documents = []
        skipped = []
        for name, content_io, content_type in files_to_upload:
            existing = self._existing(organization, name)
            if self._conflict(request, existing, name) is not None:
                skipped.append({"name": name, "reason": "A document with this name already exists."})
                continue

            storage_key = f"{request.user.pk}/{uuid.uuid4()}/{name}"
            try:
                storage.upload_fileobj(content_io, storage_key, content_type)
            except Exception:
                logger.exception("Object storage upload failed for '%s'.", name)
                skipped.append({"name": name, "reason": "Could not store the file."})
                continue

            document, _ = self._save_document(
                existing,
                organization=organization,
                name=name,
                uploader=request.user,
                defaults={
                    "is_private": data.get("is_private", False),
                    "source_type": SourceType.FILE,
                    "storage_key": storage_key,
                    "content_type": content_type,
                    "size_bytes": content_io.getbuffer().nbytes,
                },
            )
            self._queue(document)
            documents.append(document)

        if not documents:
            return Response(
                {"detail": "No files were accepted.", "skipped": skipped},
                status=status.HTTP_409_CONFLICT,
            )
        body = {"documents": DocumentSerializer(documents, many=True).data, "skipped": skipped}
        return Response(body, status=status.HTTP_202_ACCEPTED)

    @extend_schema(
        request=SourceUploadSerializer,
        responses={202: DocumentSerializer},
        description="Ingest a URL, sitemap or Confluence space.",
    )
    @action(detail=False, methods=["post"], url_path="source")
    def source(self, request):
        serializer = SourceUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        organization = request.user.organization

        existing = self._existing(organization, data["name"])
        conflict = self._conflict(request, existing, data["name"])
        if conflict is not None:
            return conflict

        document, created = self._save_document(
            existing,
            organization=organization,
            name=data["name"],
            uploader=request.user,
            defaults={
                "is_private": data.get("is_private", False),
                "source_type": data["source_type"],
                "source_uri": data["source_uri"],
                "source_options": {
                    "space_key": data.get("space_key", ""),
                    "verify_ssl": data.get("verify_ssl", True),
                },
            },
        )
        self._queue(document)
        return Response(
            DocumentSerializer(document).data,
            status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
        )

    @extend_schema(responses={202: DocumentSerializer}, description="Re-run ingestion.")
    @action(detail=True, methods=["post"])
    def reindex(self, request, pk=None):
        document = self.get_object()
        if not can_modify_document(request.user, document):
            raise PermissionDenied("Only the uploader or an organization admin can re-index this document.")
        document.status = Status.PROCESSING
        document.error_message = ""
        document.save(update_fields=["status", "error_message", "modified_at"])
        self._queue(document)
        log_document_reindex(
            actor=request.user,
            document_id=str(document.id),
            ip_address=self._get_client_ip(request),
        )
        return Response(DocumentSerializer(document).data, status=status.HTTP_202_ACCEPTED)

    @extend_schema(
        responses={200: {"type": "object", "properties": {"download_url": {"type": "string"}}}},
        description="Fresh signed download URL. Citations and stored download_url values expire (~15 min); call this at click time.",
    )
    @action(detail=True, methods=["get"], url_path="resolve-download")
    def resolve_download(self, request, pk=None):
        document = self.get_object()
        return Response({"download_url": DocumentSerializer(document).data["download_url"]})

    @extend_schema(responses={200: ChunkSerializer(many=True)}, description="List a document's chunks.")
    @action(detail=True, methods=["get"])
    def chunks(self, request, pk=None):
        document = self.get_object()
        queryset = Chunk.objects.filter(document=document)
        page = self.paginate_queryset(queryset)
        serializer = ChunkSerializer(page if page is not None else queryset, many=True)
        return (
            self.get_paginated_response(serializer.data)
            if page is not None
            else Response(serializer.data)
        )

    @extend_schema(responses={200: IngestionReportSerializer(many=True)}, description="List ingestion reports for a document.")
    @action(detail=True, methods=["get"], url_path="reports")
    def reports(self, request, pk=None):
        document = self.get_object()
        queryset = document.ingestion_reports.all()
        page = self.paginate_queryset(queryset)
        serializer = IngestionReportSerializer(page if page is not None else queryset, many=True)
        return (
            self.get_paginated_response(serializer.data)
            if page is not None
            else Response(serializer.data)
        )

    def perform_destroy(self, instance):
        """Delete vectors and the stored file alongside the row."""
        if not can_modify_document(self.request.user, instance):
            raise PermissionDenied("Only the uploader or an organization admin can delete this document.")
        delete_document(instance)
        log_document_delete(
            actor=self.request.user,
            document_id=str(instance.id),
            ip_address=self._get_client_ip(self.request),
        )

    @staticmethod
    def _existing(organization, name: str) -> Document | None:
        return Document.objects.filter(organization=organization, name=name).first()

    @staticmethod
    def _conflict(request, existing: Document | None, name: str):
        """409 unless the caller explicitly replaces a document they may modify.

        Replacing by name used to be silent, so any member could overwrite
        another member's document (or a private one) just by uploading a file
        with the same name. The message is the same whether or not the caller
        can see the existing document, so names of private files do not leak.
        """
        if existing is None:
            return None
        if _wants_replace(request) and can_modify_document(request.user, existing):
            return None
        return Response(
            {"detail": f"A document named '{name}' already exists.", "code": "duplicate_name"},
            status=status.HTTP_409_CONFLICT,
        )

    @staticmethod
    def _save_document(existing, *, organization, name, uploader, defaults):
        """Create a document, or replace `existing` (already authorised by `_conflict`)."""
        values = {
            "uploaded_by": uploader,
            "status": Status.PROCESSING,
            "error_message": "",
            "chunk_count": 0,
            "source_uri": "",
            "source_options": {},
            **defaults,
        }
        if existing is None:
            return Document.objects.create(organization=organization, name=name, **values), True

        old_key = existing.storage_key
        for field, value in values.items():
            setattr(existing, field, value)
        existing.save()
        if old_key and old_key != existing.storage_key:
            try:
                storage.delete(old_key)
            except Exception:
                logger.warning("Could not delete replaced object '%s'.", old_key, exc_info=True)
        return existing, False

    @staticmethod
    def _read_zip(zip_file, files_to_upload: list):
        """Unpack a ZIP defensively. Returns an error Response, or None on success.

        Entry count and sizes are checked from the central directory *before*
        anything is decompressed, and each read is also bounded, because the
        declared `file_size` is attacker-controlled and can lie.
        """
        limits = get_config().ingestion
        max_entry = limits.max_file_size_mb * 1024 * 1024
        max_total = limits.zip_max_total_size_mb * 1024 * 1024

        def reject(detail):
            return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with zipfile.ZipFile(zip_file, "r") as zf:
                entries = [i for i in zf.infolist() if not i.is_dir() and i.filename and not i.filename.endswith("/")]
                if len(entries) > limits.zip_max_entries:
                    return reject(f"ZIP has too many files (limit {limits.zip_max_entries}).")
                if sum(i.file_size for i in entries) > max_total:
                    return reject(f"ZIP expands to more than {limits.zip_max_total_size_mb} MB.")
                for info in entries:
                    if info.file_size > max_entry:
                        return reject(f"'{info.filename}' is larger than {limits.max_file_size_mb} MB.")
                    with zf.open(info) as handle:
                        content = handle.read(max_entry + 1)
                    if len(content) > max_entry:
                        return reject(f"'{info.filename}' is larger than {limits.max_file_size_mb} MB.")
                    candidate = SimpleUploadedFile(info.filename, content, content_type="application/octet-stream")
                    try:
                        safe_name, content_type = validate_upload(candidate)
                    except DjangoValidationError as exc:
                        return Response({"detail": exc.messages}, status=status.HTTP_400_BAD_REQUEST)
                    files_to_upload.append((safe_name, io.BytesIO(content), content_type))
        except zipfile.BadZipFile:
            return reject("Invalid ZIP file.")
        return None

    @staticmethod
    def _get_client_ip(request) -> str | None:
        """Extract client IP from request, handling proxies."""
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")

    @staticmethod
    def _queue(document: Document) -> None:
        """Dispatch ingestion after the transaction commits.

        Without `on_commit`, a fast worker can pick the task up before the row
        is visible and fail with DoesNotExist.
        """

        def dispatch():
            try:
                result = ingest_document_task.delay(str(document.id))
                Document.objects.filter(pk=document.pk).update(task_id=result.id)
            except Exception:
                logger.exception("Could not queue ingestion for %s.", document.id)
                Document.objects.filter(pk=document.pk).update(
                    status=Status.ERROR,
                    error_message="Could not queue ingestion. Is the worker running?",
                )

        transaction.on_commit(dispatch)


class ChatViewSet(viewsets.GenericViewSet):
    """Chat: retrieval-augmented answering over the caller's documents."""

    permission_classes = [IsAuthenticated]
    serializer_class = ChatRequestSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "chat"

    def get_queryset(self):
        if not getattr(self.request.user, "organization_id", None):
            return Conversation.objects.none()
        return self._conversations(self.request.user)

    @staticmethod
    def _conversations(user):
        """A user's own threads. Pre-scoping rows (no owner) are admin-only."""
        queryset = Conversation.objects.filter(organization_id=user.organization_id)
        if getattr(user, "is_org_admin", False):
            return queryset.filter(Q(user=user) | Q(user__isnull=True))
        return queryset.filter(user=user)

    @extend_schema(
        request=ChatRequestSerializer,
        responses={
            200: ChatResponseSerializer,
            # Streaming responses are text/event-stream, not JSON.
            206: OpenApiResponse(description="Server-sent events when stream=true."),
        },
        description="Ask a question. Set stream=true for server-sent events.",
    )
    @action(detail=False, methods=["post"])
    def ask(self, request):
        serializer = ChatRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        conversation = self._get_conversation(request, data.get("conversation_id"))
        document_id = self._validate_document(request, data.get("document_id"))
        history = self._history(conversation)

        Message.objects.create(
            conversation=conversation, role=Message.Role.USER, content=data["message"]
        )
        log_query(
            actor=request.user,
            query=data["message"],
            ip_address=self._get_client_ip(request),
        )
        callbacks = get_trace_callbacks(
            session_id=str(conversation.id), user_id=str(request.user.pk), tags=["chat"]
        )

        if data.get("stream"):
            return self._stream(
                request, conversation, data["message"], history, document_id, data.get("answer_mode", "default"), callbacks
            )

        result = async_to_sync(run_answer)(
            data["message"],
            organization_id=str(request.user.organization_id),
            user_id=request.user.pk,
            is_org_admin=request.user.is_org_admin,
            history=history,
            document_id=document_id,
            answer_mode=data.get("answer_mode", "default"),
            callbacks=callbacks,
        )
        Message.objects.create(
            conversation=conversation,
            role=Message.Role.ASSISTANT,
            content=result["answer"],
            citations=result["citations"],
            finish_reason=result["finish_reason"],
        )
        self._title(conversation, data["message"])
        return Response({**result, "conversation_id": str(conversation.id)})

    def _stream(self, request, conversation, message, history, document_id, answer_mode, callbacks):
        """Server-sent events: citations first, then answer tokens."""

        def event_stream():
            collected: list[str] = []
            citations: list[dict] = []
            finish_reason = ""

            async def produce():
                async for event in astream_answer(
                    message,
                    organization_id=str(request.user.organization_id),
                    user_id=request.user.pk,
                    is_org_admin=request.user.is_org_admin,
                    history=history,
                    document_id=document_id,
                    answer_mode=answer_mode,
                    callbacks=callbacks,
                ):
                    yield event

            loop = asyncio.new_event_loop()
            try:
                iterator = produce().__aiter__()
                while True:
                    try:
                        event = loop.run_until_complete(iterator.__anext__())
                    except StopAsyncIteration:
                        break
                    if event["type"] == "token":
                        collected.append(event["token"])
                    elif event["type"] == "citations":
                        citations = event["citations"]
                    elif event["type"] == "done":
                        finish_reason = event.get("finish_reason", "stop")
                    elif event["type"] == "error":
                        collected.append(event["message"])
                        finish_reason = "error"
                    yield f"data: {json.dumps(event)}\n\n"
            except Exception:
                logger.exception("Chat stream failed.")
                yield f'data: {json.dumps({"type": "error", "message": "Stream failed."})}\n\n'
            finally:
                loop.close()
                # Persist whatever was produced, even on a partial stream.
                Message.objects.create(
                    conversation=conversation,
                    role=Message.Role.ASSISTANT,
                    content="".join(collected),
                    citations=citations,
                    finish_reason=finish_reason,
                )
                self._title(conversation, message)

        response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"  # let nginx pass chunks straight through
        return response

    @extend_schema(responses={200: ConversationSerializer(many=True)})
    @action(detail=False, methods=["get"])
    def conversations(self, request):
        queryset = self.get_queryset()
        page = self.paginate_queryset(queryset)
        serializer = ConversationSerializer(page if page is not None else queryset, many=True)
        return (
            self.get_paginated_response(serializer.data)
            if page is not None
            else Response(serializer.data)
        )

    @extend_schema(responses={200: ConversationDetailSerializer})
    @action(detail=True, methods=["get", "delete"], url_path="conversations")
    def conversation_detail(self, request, pk=None):
        conversation = self.get_queryset().filter(pk=pk).first()
        if conversation is None:
            return Response(
                {"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND
            )
        if request.method == "DELETE":
            conversation.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(ConversationDetailSerializer(conversation).data)

    # --- helpers ---

    @staticmethod
    def _get_client_ip(request) -> str | None:
        """Extract client IP from request, handling proxies."""
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")

    def _get_conversation(self, request, conversation_id) -> Conversation:
        if conversation_id:
            conversation = self.get_queryset().filter(pk=conversation_id).first()
            if conversation is None:
                # Do not leak whether the id exists for another user.
                raise ValidationError({"conversation_id": "Unknown conversation."})
            return conversation
        return Conversation.objects.create(
            organization_id=request.user.organization_id, user=request.user
        )

    def _validate_document(self, request, document_id) -> str | None:
        if not document_id:
            return None
        exists = visible_documents(request.user).filter(pk=document_id).exists()
        if not exists:
            raise ValidationError({"document_id": "Unknown document."})
        return str(document_id)

    def _history(self, conversation: Conversation) -> list[dict]:
        messages = conversation.messages.order_by("created_at").values("role", "content")
        return list(messages)

    @staticmethod
    def _title(conversation: Conversation, first_message: str) -> None:
        if not conversation.title:
            conversation.title = first_message[:80]
            conversation.save(update_fields=["title", "modified_at"])
        else:
            conversation.save(update_fields=["modified_at"])


class AdminDashboardView(APIView):
    """Aggregated ingestion quality dashboard for admins."""

    permission_classes = [IsOrgAdmin]

    @extend_schema(
        responses={
            200: {
                "type": "object",
                "properties": {
                    "total_documents": {"type": "integer"},
                    "quality_distribution": {
                        "type": "object",
                        "properties": {
                            "GOOD": {"type": "integer"},
                            "WARNING": {"type": "integer"},
                            "BAD": {"type": "integer"},
                        },
                    },
                    "documents_with_warnings": {"type": "integer"},
                    "recent_failed_ingestions": {"type": "integer"},
                    "avg_ingestion_duration_seconds": {"type": "number"},
                },
            }
        },
        description="Admin dashboard with ingestion quality summary.",
    )
    def get(self, request):
        documents = Document.objects.filter(organization_id=request.user.organization_id)

        total = documents.count()
        quality_dist = {"GOOD": 0, "WARNING": 0, "BAD": 0}
        documents_with_warnings = 0
        total_duration = 0.0
        duration_count = 0
        recent_failed = 0

        for doc in documents:
            report = doc.last_ingestion_report
            if report:
                quality_dist[report.quality_score] = quality_dist.get(report.quality_score, 0) + 1
                if report.warnings:
                    documents_with_warnings += 1
                if report.ingestion_duration_seconds > 0:
                    total_duration += report.ingestion_duration_seconds
                    duration_count += 1
            if doc.status == Status.ERROR:
                recent_failed += 1

        avg_duration = total_duration / duration_count if duration_count > 0 else 0.0

        return Response({
            "total_documents": total,
            "quality_distribution": quality_dist,
            "documents_with_warnings": documents_with_warnings,
            "recent_failed_ingestions": recent_failed,
            "avg_ingestion_duration_seconds": round(avg_duration, 2),
        })


class CorpusReadinessView(APIView):
    """Tell an organization exactly which sources are queryable and why."""

    permission_classes = [IsOrgAdmin]

    @extend_schema(
        responses={200: OpenApiResponse(description="Organization corpus readiness report.")},
        description="Report indexed documents, failed ingestions, and warnings.",
    )
    def get(self, request):
        documents = Document.objects.filter(
            organization_id=request.user.organization_id
        ).select_related("last_ingestion_report")
        total = documents.count()
        ready_documents = list(documents.filter(status=Status.READY))
        queryable_documents = list(
            documents.filter(status__in=[Status.ENRICHING, Status.READY])
        )

        failed = [
            {
                "id": str(document.id),
                "name": document.name,
                "reason": document.error_message or "Unknown ingestion error.",
                "quality": (
                    document.last_ingestion_report.quality_score
                    if document.last_ingestion_report
                    else None
                ),
            }
            for document in documents.filter(status=Status.ERROR)
        ]
        needs_attention = [
            {
                "id": str(document.id),
                "name": document.name,
                "warnings": document.last_ingestion_report.warnings,
            }
            for document in queryable_documents
            if document.last_ingestion_report
            and document.last_ingestion_report.warnings
        ]
        ready_needs_attention = [
            document
            for document in ready_documents
            if document.last_ingestion_report and document.last_ingestion_report.warnings
        ]

        return Response(
            {
                "total_documents": total,
                "queryable_documents": len(queryable_documents),
                "enriching_documents": sum(
                    1 for document in queryable_documents if document.status == Status.ENRICHING
                ),
                "fully_indexed": len(ready_documents) - len(ready_needs_attention),
                "indexed_with_warnings": len(needs_attention),
                "failed": failed,
                "needs_attention": needs_attention,
                "readiness_percent": round(100 * len(ready_documents) / total, 1)
                if total
                else 0.0,
            }
        )


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only audit log for compliance queries."""

    permission_classes = [IsOrgAdmin]
    serializer_class = AuditLogSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "audit"

    def get_queryset(self):
        return AuditLog.objects.filter(
            organization_id=self.request.user.organization_id
        ).order_by("-created_at")


class EvaluationReportViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only evaluation reports for RAG quality metrics."""

    permission_classes = [IsAuthenticated]
    serializer_class = EvaluationReportSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "evaluation"

    def get_queryset(self):
        return EvaluationReport.objects.filter(
            document__in=visible_documents(self.request.user)
        ).order_by("-created_at")
