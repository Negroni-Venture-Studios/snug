"""Rooms, their folders, and which documents they hold.

A room has at most one level of folders. A document can be in any number of
rooms (decided 5 November 2026), at most once in each.
"""

from django.conf import settings
from django.db import models
from django.db.models.functions import Lower

from snug.documents.models import Document, new_token


class Room(models.Model):
    name = models.CharField(max_length=120)
    token = models.CharField(max_length=32, unique=True, default=new_token, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class Folder(models.Model):
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="folders")
    name = models.CharField(max_length=80)

    class Meta:
        ordering = ("name",)
        constraints = (
            models.UniqueConstraint(Lower("name"), "room", name="folder_name_unique_in_room"),
        )

    def __str__(self) -> str:
        return self.name


class RoomDocument(models.Model):
    """A document's place in a room: in a folder, or at the top level."""

    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="entries")
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="room_entries")
    folder = models.ForeignKey(
        Folder, null=True, blank=True, on_delete=models.SET_NULL, related_name="entries"
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=("room", "document"), name="document_once_per_room"),
        )

    def __str__(self) -> str:
        return f"{self.document} in {self.room}"
