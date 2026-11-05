from django.urls import path

from snug.audit import views

app_name = "audit"

urlpatterns = [
    path("", views.audit_log, name="list"),
    path("export.csv", views.audit_export, name="export"),
]
