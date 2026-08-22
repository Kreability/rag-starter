"""Tests for admin dashboard."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIClient

from rag.models import Document, IngestionReport, QualityScore, Status


def test_admin_dashboard_returns_summary() -> None:
    client = APIClient()
    user = MagicMock()
    user.is_authenticated = True
    client.force_authenticate(user=user)

    mock_documents = MagicMock()
    mock_documents.count.return_value = 10
    mock_documents.filter.return_value = mock_documents

    with patch("rag.views.Document.objects.filter", return_value=mock_documents), \
         patch("rag.views.IngestionReport.objects.filter", return_value=MagicMock()):
        from rag.views import AdminDashboardView
        view = AdminDashboardView()
        request = MagicMock()
        request.user = user
        response = view.get(request)

    assert response.status_code == 200
    assert "total_documents" in response.data
    assert "quality_distribution" in response.data
    assert "documents_with_warnings" in response.data
    assert "recent_failed_ingestions" in response.data
    assert "avg_ingestion_duration_seconds" in response.data
