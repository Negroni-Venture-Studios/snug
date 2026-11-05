from django.urls import path

from snug.links import views

app_name = "links"

urlpatterns = [
    path("", views.link_list, name="list"),
    path("for/<str:document_token>", views.link_new, name="new"),
    path("for-room/<str:room_token>", views.link_new_room, name="new_room"),
    path("issued", views.link_issued, name="issued"),
    path("<str:ref>/revoke", views.link_revoke, name="revoke"),
    path("<str:ref>/reissue", views.link_reissue, name="reissue"),
]
