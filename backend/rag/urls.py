from django.urls import path
from rest_framework import routers

from rag.views import AdminDashboardView, AuditLogViewSet, ChatViewSet, DocumentViewSet, EvaluationReportViewSet

router = routers.DefaultRouter()
router.register("documents", DocumentViewSet, basename="rag-documents")
router.register("chat", ChatViewSet, basename="rag-chat")
router.register("audit-logs", AuditLogViewSet, basename="rag-audit-logs")
router.register("evaluations", EvaluationReportViewSet, basename="rag-evaluations")

urlpatterns = [
    path("admin/dashboard", AdminDashboardView.as_view(), name="admin-dashboard"),
    *router.urls,
]
