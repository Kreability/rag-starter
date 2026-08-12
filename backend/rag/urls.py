from rest_framework import routers

from rag.views import ChatViewSet, DocumentViewSet

router = routers.DefaultRouter()
router.register("documents", DocumentViewSet, basename="rag-documents")
router.register("chat", ChatViewSet, basename="rag-chat")

urlpatterns = router.urls
