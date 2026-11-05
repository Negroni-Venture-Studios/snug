"""Visits and time per page. Kept on the instance; never sent anywhere."""

from django.db import models

from snug.documents.models import Document
from snug.links.models import Link
from snug.viewer.models import ViewerSession


class Visit(models.Model):
    """One sitting with a document: pages turned with no gap of half an hour."""

    # Random, carried by the reader page for its heartbeats. Not a database key.
    token = models.CharField(max_length=32, unique=True)
    link = models.ForeignKey(Link, on_delete=models.CASCADE, related_name="visits")
    session = models.ForeignKey(
        ViewerSession, null=True, blank=True, on_delete=models.SET_NULL, related_name="visits"
    )
    # Kept here as well, so reading history outlives the session that made it.
    email = models.EmailField()
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="visits")
    version_number = models.PositiveIntegerField()
    started_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()
    # The page being shown, and when time on it was last credited.
    current_page = models.PositiveIntegerField()
    clock_at = models.DateTimeField()

    class Meta:
        ordering = ("-started_at",)
        indexes = (models.Index(fields=("document", "email")),)

    def __str__(self) -> str:
        return f"{self.email} reading {self.document_id}"


class PageView(models.Model):
    """Time on one page during one visit."""

    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="pages")
    page = models.PositiveIntegerField()
    seconds = models.FloatField(default=0)
    first_seen_at = models.DateTimeField()

    class Meta:
        constraints = (models.UniqueConstraint(fields=("visit", "page"), name="one_row_per_page"),)

    def __str__(self) -> str:
        return f"Page {self.page}: {self.seconds:.0f} s"
