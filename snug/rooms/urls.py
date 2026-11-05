from django.urls import path

from snug.rooms import views

app_name = "rooms"

# Admin URLs. Rooms and documents are addressed by their random tokens.
urlpatterns = [
    path("", views.room_list, name="list"),
    path("new", views.room_new, name="new"),
    path("<str:token>", views.room_detail, name="detail"),
    path("<str:token>/rename", views.room_rename, name="rename"),
    path("<str:token>/folders", views.folder_add, name="folder_add"),
    path("<str:token>/folders/<int:folder_id>/remove", views.folder_remove, name="folder_remove"),
    path("<str:token>/documents", views.document_add, name="document_add"),
    path(
        "<str:token>/documents/<str:document_token>/move", views.document_move, name="document_move"
    ),
    path(
        "<str:token>/documents/<str:document_token>/remove",
        views.document_remove,
        name="document_remove",
    ),
]
