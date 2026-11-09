from django.urls import path

from snug.documents import views

app_name = "documents"

# Admin URLs. Documents are addressed by their random token here too, although
# only viewer URLs are required to avoid database keys.
urlpatterns = [
    path("", views.document_list, name="list"),
    path("new", views.document_new, name="new"),
    path("<str:token>", views.document_detail, name="detail"),
    path("<str:token>/versions", views.version_upload, name="upload_version"),
    path("<str:token>/description", views.description, name="description"),
    path("<str:token>/v<int:number>/page-<int:page>.webp", views.page_preview, name="preview"),
]
