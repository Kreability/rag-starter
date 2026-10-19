"""Every registered admin page must render.

Fieldset options are passed straight into Django's `Fieldset`, so an option the
installed Django/Unfold does not understand (e.g. `icon`) only fails when that
change page is opened. Open each one.
"""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import Client

from api.models import Organization
from rag.models import (
    AuditLog,
    Chunk,
    Conversation,
    Document,
    EvaluationReport,
    EvaluationRun,
    IngestionReport,
    Message,
    UsageRecord,
)


@pytest.fixture
def staff_client(db):
    org = Organization.objects.create(name="Acme", slug="acme-pages")
    user = get_user_model().objects.create_superuser(
        username="root@example.com", email="root@example.com", password="pw", organization=org
    )
    client = Client()
    client.force_login(user)
    client.org = org
    client.user = user
    return client


def _make_objects(org, user):
    document = Document.objects.create(organization=org, uploaded_by=user, name="a.txt")
    report = IngestionReport.objects.create(document=document)
    conversation = Conversation.objects.create(organization=org, user=user)
    return [
        document,
        report,
        Chunk.objects.create(document=document, content_hash="h1", content="text"),
        conversation,
        Message.objects.create(conversation=conversation, role="user", content="hi"),
        EvaluationReport.objects.create(document=document, query="q", answer="a"),
        EvaluationRun.objects.create(organization=org, triggered_by=user),
        AuditLog.objects.create(action=AuditLog.Action.QUERY, organization=org, actor=user),
        UsageRecord.objects.create(organization=org, operation="query", model="m"),
    ]


def test_every_rag_admin_changelist_and_change_page_renders(staff_client):
    objects = _make_objects(staff_client.org, staff_client.user)
    registered = {model for model in admin.site._registry if model._meta.app_label == "rag"}
    covered = {type(obj) for obj in objects}
    assert registered <= covered, f"add a fixture for: {registered - covered}"

    for obj in objects:
        meta = obj._meta
        changelist = staff_client.get(f"/admin/{meta.app_label}/{meta.model_name}/")
        assert changelist.status_code == 200, f"{meta.model_name} changelist"
        change = staff_client.get(f"/admin/{meta.app_label}/{meta.model_name}/{obj.pk}/change/")
        assert change.status_code == 200, f"{meta.model_name} change page"
