from django.urls import path

from snug.analytics import views

app_name = "analytics"

urlpatterns = [
    path("", views.people, name="people"),
]
