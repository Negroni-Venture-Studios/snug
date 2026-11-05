"""Viewer URLs. Every one carries the link's random token, never a database key."""

from django.urls import path

from snug.viewer import views

app_name = "viewer"

urlpatterns = [
    path("<str:token>", views.entry, name="entry"),
    path("<str:token>/code/send", views.send_code, name="send_code"),
    path("<str:token>/code", views.verify_code, name="verify_code"),
    path("<str:token>/open", views.open_link, name="open"),
]
