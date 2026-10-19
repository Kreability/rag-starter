"""Regression tests for the P0 audit findings (see todo.md)."""

from __future__ import annotations

import io
import zipfile
from unittest.mock import MagicMock, patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from api.models import Organization, Role
from api.tests.factories import UserFactory
from rag.conf import get_config
from rag.models import Conversation, Document, Message, Status
from rag.security import FetchError, safe_get, validate_public_url


@pytest.fixture
def org(db):
    return Organization.objects.create(name="Acme", slug="acme-p0")


@pytest.fixture
def alice(org):
    return UserFactory.create(organization=org, username="alice@example.com", email="alice@example.com")


@pytest.fixture
def bob(org):
    return UserFactory.create(organization=org, username="bob@example.com", email="bob@example.com")


@pytest.fixture
def admin(org):
    return UserFactory.create(
        organization=org, username="admin@example.com", email="admin@example.com", role=Role.ADMIN
    )


def client_for(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


# --- conversations are per-user ------------------------------------------------


class TestConversationScoping:
    def _conversation(self, org, user, title="private chat"):
        conversation = Conversation.objects.create(organization=org, user=user, title=title)
        Message.objects.create(conversation=conversation, role="user", content="secret question")
        return conversation

    def test_user_cannot_list_a_colleagues_conversations(self, org, alice, bob):
        self._conversation(org, alice)
        response = client_for(bob).get("/api/chat/conversations/")
        assert response.status_code == 200
        rows = response.data["results"] if isinstance(response.data, dict) else response.data
        assert rows == []

    def test_user_cannot_read_or_delete_a_colleagues_conversation(self, org, alice, bob):
        conversation = self._conversation(org, alice)
        client = client_for(bob)
        assert client.get(f"/api/chat/{conversation.id}/conversations/").status_code == 404
        assert client.delete(f"/api/chat/{conversation.id}/conversations/").status_code == 404
        assert Conversation.objects.filter(pk=conversation.pk).exists()

    def test_owner_can_read_own_conversation(self, org, alice):
        conversation = self._conversation(org, alice)
        assert client_for(alice).get(f"/api/chat/{conversation.id}/conversations/").status_code == 200

    def test_ask_cannot_continue_a_colleagues_conversation(self, org, alice, bob):
        conversation = self._conversation(org, alice)
        response = client_for(bob).post(
            "/api/chat/ask/", {"message": "hi", "conversation_id": str(conversation.id)}, format="json"
        )
        assert response.status_code == 400
        assert conversation.messages.count() == 1

    def test_new_conversations_record_their_owner(self, org, alice):
        async def fake_answer(*args, **kwargs):
            return {"answer": "ok", "citations": [], "confidence": "unknown", "finish_reason": "stop"}

        with patch("rag.views.run_answer", fake_answer):
            response = client_for(alice).post("/api/chat/ask/", {"message": "hello there"}, format="json")
        assert response.status_code == 200
        assert Conversation.objects.get(pk=response.data["conversation_id"]).user == alice

    def test_legacy_ownerless_conversations_are_admin_only(self, org, alice, admin):
        legacy = Conversation.objects.create(organization=org, user=None, title="legacy")
        assert client_for(alice).get(f"/api/chat/{legacy.id}/conversations/").status_code == 404
        assert client_for(admin).get(f"/api/chat/{legacy.id}/conversations/").status_code == 200

    def test_admin_does_not_see_other_users_threads(self, org, alice, admin):
        conversation = self._conversation(org, alice)
        assert client_for(admin).get(f"/api/chat/{conversation.id}/conversations/").status_code == 404

    def test_document_scope_accepts_a_visible_document(self, org, alice):
        document = Document.objects.create(organization=org, uploaded_by=alice, name="a.txt")

        async def fake_answer(*args, **kwargs):
            return {"answer": "ok", "citations": [], "confidence": "unknown", "finish_reason": "stop"}

        with patch("rag.views.run_answer", fake_answer):
            response = client_for(alice).post(
                "/api/chat/ask/", {"message": "hello there", "document_id": str(document.id)}, format="json"
            )
        assert response.status_code == 200, response.data


# --- documents are not replaced silently -----------------------------------------


def _upload(client, name="report.txt", content=b"hello world, this is a test document.", **extra):
    file = SimpleUploadedFile(name, content, content_type="text/plain")
    return client.post("/api/documents/upload/", {"file": file, **extra}, format="multipart")


@pytest.fixture
def stub_io():
    with patch("rag.views.storage.upload_fileobj") as upload, \
         patch("rag.views.storage.delete") as delete, \
         patch("rag.views.ingest_document_task.delay", return_value=MagicMock(id="t1")), \
         patch("rag.views.transaction.on_commit", lambda fn: fn()):
        yield {"upload": upload, "delete": delete}


class TestDuplicateNames:
    def test_duplicate_name_is_rejected_with_409(self, org, alice, bob, stub_io):
        assert _upload(client_for(alice)).status_code == 202
        response = _upload(client_for(bob))
        assert response.status_code == 409
        assert response.data["code"] == "duplicate_name"
        assert Document.objects.get(organization=org, name="report.txt").uploaded_by == alice

    def test_rejected_duplicate_does_not_store_bytes(self, org, alice, bob, stub_io):
        _upload(client_for(alice))
        stub_io["upload"].reset_mock()
        _upload(client_for(bob))
        stub_io["upload"].assert_not_called()

    def test_other_user_cannot_force_replace(self, org, alice, bob, stub_io):
        _upload(client_for(alice))
        assert _upload(client_for(bob), replace="true").status_code == 409

    def test_uploader_can_replace_and_old_object_is_deleted(self, org, alice, stub_io):
        first = _upload(client_for(alice))
        old_key = Document.objects.get(pk=first.data["id"]).storage_key
        response = _upload(client_for(alice), content=b"new content for the document", replace="true")
        assert response.status_code == 200
        document = Document.objects.get(organization=org, name="report.txt")
        assert document.storage_key != old_key
        stub_io["delete"].assert_called_with(old_key)

    def test_admin_can_replace(self, org, alice, admin, stub_io):
        _upload(client_for(alice))
        assert _upload(client_for(admin), replace="true").status_code == 200

    def test_source_duplicate_is_rejected(self, org, alice, bob, stub_io):
        Document.objects.create(organization=org, uploaded_by=alice, name="https://example.com/a")
        with patch("rag.serializers.validate_public_url", side_effect=lambda u: u):
            response = client_for(bob).post(
                "/api/documents/source/",
                {"source_type": "URL", "source_uri": "https://example.com/a"},
                format="json",
            )
        assert response.status_code == 409

    def test_bulk_upload_skips_duplicates(self, org, alice, bob, stub_io):
        _upload(client_for(alice), name="dup.txt")
        files = [
            SimpleUploadedFile("dup.txt", b"some duplicate text here", content_type="text/plain"),
            SimpleUploadedFile("fresh.txt", b"brand new file content", content_type="text/plain"),
        ]
        response = client_for(bob).post("/api/documents/bulk-upload/", {"files": files}, format="multipart")
        assert response.status_code == 202
        assert [d["name"] for d in response.data["documents"]] == ["fresh.txt"]
        assert response.data["skipped"][0]["name"] == "dup.txt"


# --- only the uploader or an admin may delete / re-index -----------------------


class TestDocumentModification:
    @pytest.fixture
    def doc(self, org, alice):
        return Document.objects.create(organization=org, uploaded_by=alice, name="shared.pdf", status=Status.READY)

    def test_other_member_cannot_delete(self, doc, bob):
        with patch("rag.views.delete_document") as deleter:
            response = client_for(bob).delete(f"/api/documents/{doc.id}/")
        assert response.status_code == 403
        deleter.assert_not_called()

    def test_other_member_cannot_reindex(self, doc, bob, stub_io):
        assert client_for(bob).post(f"/api/documents/{doc.id}/reindex/").status_code == 403

    def test_uploader_and_admin_can_delete(self, doc, alice, admin):
        for user in (alice, admin):
            document = Document.objects.create(organization=doc.organization, uploaded_by=alice, name=f"x-{user.pk}.pdf")
            with patch("rag.views.delete_document") as deleter:
                assert client_for(user).delete(f"/api/documents/{document.id}/").status_code == 204
            deleter.assert_called_once()

    def test_admin_can_reindex(self, doc, admin, stub_io):
        assert client_for(admin).post(f"/api/documents/{doc.id}/reindex/").status_code == 202


# --- Confluence token is host-scoped -----------------------------------------------


class TestConfluenceToken:
    def test_token_only_released_for_the_configured_host(self, monkeypatch):
        from rag.ingest import _configured_confluence_token

        monkeypatch.setenv("CONFLUENCE_TOKEN", "s3cret")
        monkeypatch.setenv("CONFLUENCE_URL", "https://wiki.example.com")
        assert _configured_confluence_token("https://wiki.example.com/spaces") == "s3cret"
        assert _configured_confluence_token("https://evil.example.org") == ""

    def test_no_configured_host_means_no_token(self, monkeypatch):
        from rag.ingest import _configured_confluence_token

        monkeypatch.setenv("CONFLUENCE_TOKEN", "s3cret")
        monkeypatch.delenv("CONFLUENCE_URL", raising=False)
        assert _configured_confluence_token("https://wiki.example.com") == ""


# --- SSRF: redirects, ports, size -------------------------------------------------


def _response(status=200, headers=None, body=b"", chunks=None):
    response = MagicMock()
    response.status_code = status
    response.headers = headers or {}
    response.is_redirect = status in (301, 302, 303, 307, 308)
    response.is_permanent_redirect = status in (301, 308)
    response.iter_content.return_value = chunks if chunks is not None else [body]
    response.raise_for_status.return_value = None
    return response


class TestSafeGet:
    def test_redirect_to_a_private_address_is_blocked(self):
        redirect = _response(302, {"Location": "http://169.254.169.254/latest/meta-data/"})
        with patch("rag.security.validate_public_url", side_effect=lambda u: validate_public_url(u) if "169" in u else u), \
             patch("requests.get", return_value=redirect) as get:
            with pytest.raises(FetchError):
                safe_get("https://public.example.com/")
        assert get.call_count == 1  # the metadata URL was never requested

    def test_redirects_are_not_followed_by_requests(self):
        with patch("rag.security.validate_public_url", side_effect=lambda u: u), \
             patch("requests.get", return_value=_response(200, body=b"ok")) as get:
            safe_get("https://public.example.com/")
        assert get.call_args.kwargs["allow_redirects"] is False

    def test_follows_safe_redirects(self):
        responses = [_response(302, {"Location": "/next"}), _response(200, body=b"final")]
        with patch("rag.security.validate_public_url", side_effect=lambda u: u), \
             patch("requests.get", side_effect=responses):
            assert safe_get("https://public.example.com/start")._content == b"final"

    def test_redirect_loops_are_capped(self):
        loop = _response(302, {"Location": "https://public.example.com/again"})
        with patch("rag.security.validate_public_url", side_effect=lambda u: u), \
             patch("requests.get", return_value=loop):
            with pytest.raises(FetchError, match="redirects"):
                safe_get("https://public.example.com/")

    def test_oversized_body_is_rejected(self):
        limit = get_config().ingestion.max_fetch_size_mb * 1024 * 1024
        big = _response(200, chunks=[b"x" * (limit // 2 + 1)] * 2)
        with patch("rag.security.validate_public_url", side_effect=lambda u: u), \
             patch("requests.get", return_value=big):
            with pytest.raises(FetchError, match="larger"):
                safe_get("https://public.example.com/")

    def test_oversized_content_length_is_rejected_early(self):
        limit = get_config().ingestion.max_fetch_size_mb * 1024 * 1024
        declared = _response(200, {"Content-Length": str(limit + 1)})
        with patch("rag.security.validate_public_url", side_effect=lambda u: u), \
             patch("requests.get", return_value=declared):
            with pytest.raises(FetchError):
                safe_get("https://public.example.com/")
        declared.iter_content.assert_not_called()

    @pytest.mark.parametrize("port", [6333, 6334, 9000, 9001])
    def test_infrastructure_ports_are_blocked(self, port):
        from django.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            validate_public_url(f"http://example.com:{port}/")


# --- zip bombs ------------------------------------------------------------------------


def _zip(entries: dict[str, bytes]) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return SimpleUploadedFile("bundle.zip", buffer.getvalue(), content_type="application/zip")


class TestZipLimits:
    def _post(self, user, archive):
        return client_for(user).post("/api/documents/bulk-upload/", {"zip_file": archive}, format="multipart")

    def test_valid_zip_is_accepted(self, alice, stub_io):
        response = self._post(alice, _zip({"a.txt": b"first document text", "b.txt": b"second document text"}))
        assert response.status_code == 202
        assert len(response.data["documents"]) == 2

    def test_too_many_entries_rejected_before_reading(self, alice, stub_io, monkeypatch):
        monkeypatch.setattr(get_config().ingestion, "zip_max_entries", 3)
        archive = _zip({f"f{i}.txt": b"some text" for i in range(5)})
        with patch("zipfile.ZipFile.open") as opened:
            response = self._post(alice, archive)
        assert response.status_code == 400 and "too many" in response.data["detail"]
        opened.assert_not_called()

    def test_total_expanded_size_is_capped(self, alice, stub_io, monkeypatch):
        monkeypatch.setattr(get_config().ingestion, "zip_max_total_size_mb", 1)
        archive = _zip({"a.txt": b"a" * 700_000, "b.txt": b"b" * 700_000})
        response = self._post(alice, archive)
        assert response.status_code == 400 and "expands" in response.data["detail"]

    def test_single_oversized_entry_rejected(self, alice, stub_io, monkeypatch):
        monkeypatch.setattr(get_config().ingestion, "max_file_size_mb", 1)
        response = self._post(alice, _zip({"big.txt": b"z" * 2_000_000}))
        assert response.status_code == 400 and "larger" in response.data["detail"]
