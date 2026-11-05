from django.urls import path

from snug.core import views

app_name = "core"

urlpatterns = [
    path("", views.overview, name="overview"),
]
