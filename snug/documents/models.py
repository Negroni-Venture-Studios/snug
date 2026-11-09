"""Documents and their versions.

A link points at a document and always shows its latest ready version;
analytics record which version was read. Originals are stored under a
generated name, never the name they were uploaded with.

A document is a PDF, read page by page, or an Excel workbook (decided
9 November 2026), shared for download only. Its versions are all one kind.
"""

import secrets
import uuid

from django.conf import settings
from django.db import models


def new_token() -> str:
    """128 random bits. Viewer URLs use this, never the database key."""
    return secrets.token_urlsafe(16)


def new_stored_name(extension: str = "pdf") -> str:
    return f"{uuid.uuid4().hex}.{extension}"


MAX_DESCRIPTION = 1000


class Document(models.Model):
    title = models.CharField(max_length=200)
    # A workbook's own words for investors, above its sheets: plain text, kept
    # from version to version. A link's cover message is for one investor.
    description = models.TextField(blank=True, max_length=MAX_DESCRIPTION)
    token = models.CharField(max_length=32, unique=True, default=new_token, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ("title",)

    def __str__(self) -> str:
        return self.title

    def latest_version(self) -> "DocumentVersion | None":
        return self.versions.filter(status=DocumentVersion.Status.READY).order_by("-number").first()


class DocumentVersion(models.Model):
    class Status(models.TextChoices):
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    class Kind(models.TextChoices):
        PDF = "pdf", "PDF"
        XLSX = "xlsx", "Excel workbook"

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField()
    # The file's name on disk: generated, never derived from the upload.
    stored_name = models.CharField(
        max_length=40, unique=True, default=new_stored_name, editable=False
    )
    # The name the file was uploaded with. Shown to admins; never used as a path.
    original_filename = models.CharField(max_length=255, blank=True)
    size_bytes = models.BigIntegerField()
    sha256 = models.CharField(max_length=64)
    page_count = models.PositiveIntegerField()
    # [[width, height], ...] in PDF points, one pair per page.
    page_sizes = models.JSONField(default=list)
    kind = models.CharField(max_length=4, choices=Kind.choices, default=Kind.PDF)
    # A workbook's visible sheets, in order; hidden sheets are never listed.
    sheets = models.JSONField(default=list)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.READY)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ("document", "-number")
        constraints = (
            models.UniqueConstraint(fields=("document", "number"), name="version_number_unique"),
        )

    def __str__(self) -> str:
        return f"{self.document} v{self.number}"

    @property
    def is_workbook(self) -> bool:
        return self.kind == self.Kind.XLSX

    @property
    def describe(self) -> str:
        """For admins and investors: "12 pages", or "Excel workbook, 3 sheets"."""
        if self.is_workbook:
            count = len(self.sheets)
            return f"Excel workbook, {count} sheet{'' if count == 1 else 's'}"
        return f"{self.page_count} page{'' if self.page_count == 1 else 's'}"
