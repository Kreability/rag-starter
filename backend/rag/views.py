"""REST API.

Replaces upstream's `rag_api.py` (chat) and `admin_api.py` (documents) with two
DRF viewsets. Every queryset is filtered by `request.user`: authentication alone
is not authorisation, and documents are per-user data.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from asgiref.sync import async_to_sync
from django.db import transaction
from django.http import StreamingHttpResponse
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from rag import storage
from rag.graph import answer as run_answer
from rag.graph import astream_answer
from rag.ingest import delete_document
from rag.llm import get_trace_callbacks
from rag.models import Chunk, Conversation, Document, Message, SourceType, Status
from rag.serializers import (
    ChatRequestSerializer,
    ChatResponseSerializer,
    ChunkSerializer,
    ConversationDetailSerializer,
    ConversationSerializer,
    DocumentSerializer,
    DocumentUploadSerializer,
    SourceUploadSerializer,
)
from rag.tasks import ingest_document_task

logger = logging.getLogger(__name__)


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
        # Tenant isolation: a user only ever sees their own documents.
        return Document.objects.filter(owner=self.request.user)

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

        name = uploaded.sanitized_name
        content_type = uploaded.resolved_content_type
        storage_key = f"{request.user.pk}/{uuid.uuid4()}/{name}"

        # Store the bytes before creating the row, so a READY row always has a file.
        try:
            storage.upload_fileobj(uploaded, storage_key, content_type)
        except Exception:
            logger.exception("Object storage upload failed for '%s'.", name)
            return Response(
                {"detail": "Could not store the file. Please try again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        document, created = Document.objects.update_or_create(
            owner=request.user,
            name=name,
            defaults={
                "source_type": SourceType.FILE,
                "status": Status.PROCESSING,
                "storage_key": storage_key,
                "content_type": content_type,
                "size_bytes": uploaded.size,
                "error_message": "",
                "chunk_count": 0,
            },
        )
        self._queue(document)
        return Response(
            DocumentSerializer(document).data,
            status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
        )

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

        document, created = Document.objects.update_or_create(
            owner=request.user,
            name=data["name"],
            defaults={
                "source_type": data["source_type"],
                "source_uri": data["source_uri"],
                "status": Status.PROCESSING,
                "source_options": {
                    "space_key": data.get("space_key", ""),
                    "verify_ssl": data.get("verify_ssl", True),
                },
                "error_message": "",
                "chunk_count": 0,
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
        document.status = Status.PROCESSING
        document.error_message = ""
        document.save(update_fields=["status", "error_message", "modified_at"])
        self._queue(document)
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

    def perform_destroy(self, instance):
        """Delete vectors and the stored file alongside the row."""
        delete_document(instance)

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
        return Conversation.objects.filter(owner=self.request.user)

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
        callbacks = get_trace_callbacks(
            session_id=str(conversation.id), user_id=str(request.user.pk), tags=["chat"]
        )

        if data.get("stream"):
            return self._stream(
                request, conversation, data["message"], history, document_id, callbacks
            )

        result = async_to_sync(run_answer)(
            data["message"],
            owner_id=request.user.pk,
            history=history,
            document_id=document_id,
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

    def _stream(self, request, conversation, message, history, document_id, callbacks):
        """Server-sent events: citations first, then answer tokens."""

        def event_stream():
            collected: list[str] = []
            citations: list[dict] = []
            finish_reason = ""

            async def produce():
                async for event in astream_answer(
                    message,
                    owner_id=request.user.pk,
                    history=history,
                    document_id=document_id,
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

    def _get_conversation(self, request, conversation_id) -> Conversation:
        if conversation_id:
            conversation = Conversation.objects.filter(
                pk=conversation_id, owner=request.user
            ).first()
            if conversation is None:
                # Do not leak whether the id exists for another user.
                raise ValidationError({"conversation_id": "Unknown conversation."})
            return conversation
        return Conversation.objects.create(owner=request.user)

    def _validate_document(self, request, document_id) -> str | None:
        if not document_id:
            return None
        exists = Document.objects.filter(pk=document_id, owner=request.user).exists()
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
