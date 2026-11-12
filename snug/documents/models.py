"""Documents and their versions.

A link points at a document and always shows its latest ready version;
analytics record which version was read. Originals are stored under a
generated name, never the name they were uploaded with.

A document is a PDF, read page by page; an Office file, shared for download
(an Excel workbook, decided 9 November 2026; a Word document or PowerPoint
presentation, 12 November 2026); or an MP4 video (9 November), watched in
the browser. Its versions are all one kind.
"""

import secrets
import uuid
from dataclasses import dataclass

from django.conf import settings
from django.db import models


def new_token() -> str:
    """128 random bits. Viewer URLs use this, never the database key."""
    return secrets.token_urlsafe(16)


def new_stored_name(extension: str = "pdf") -> str:
    return f"{uuid.uuid4().hex}.{extension}"


MAX_DESCRIPTION = 1000
# A video is counted in parts of this many seconds, each treated as a page.
PART_SECONDS = 10


@dataclass(frozen=True)
class OfficeFormat:
    """How Snug names an Office file to admins and investors, and serves it."""

    name: str  # "Excel workbook"
    a_name: str  # "an Excel workbook"
    short: str  # "workbook", as in "Download the workbook"
    content_type: str
    extension: str


OFFICE = {
    "xlsx": OfficeFormat(
        "Excel workbook",
        "an Excel workbook",
        "workbook",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
    "docx": OfficeFormat(
        "Word document",
        "a Word document",
        "Word document",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
    "pptx": OfficeFormat(
        "PowerPoint presentation",
        "a PowerPoint presentation",
        "presentation",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
    ),
}


class Document(models.Model):
    title = models.CharField(max_length=200)
    # An Office file's own words for investors, on its card: plain text, kept
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
        # A new version of an Office file, waiting for its preview before links
        # show it (decided 12 November 2026); links show the version before.
        PREPARING = "preparing", "Preparing"

    class Preview(models.TextChoices):
        """Where an Office file's preview is: made by the LibreOffice converter (snug.office)."""

        NONE = "", "None"
        PENDING = "pending", "Waiting for the converter"
        CONVERTING = "converting", "Converting"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    class Kind(models.TextChoices):
        PDF = "pdf", "PDF"
        XLSX = "xlsx", "Excel workbook"
        DOCX = "docx", "Word document"
        PPTX = "pptx", "PowerPoint presentation"
        MP4 = "mp4", "MP4 video"

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
    # A presentation's slide count, from its own list of slides.
    slides = models.PositiveIntegerField(default=0)
    # An Office file's preview, when the converter is installed. Once it is
    # ready, page_count and page_sizes are the preview PDF's, so it is read
    # page by page like a PDF; the download is still the stamped original.
    preview = models.CharField(max_length=10, choices=Preview.choices, blank=True, default="")
    preview_error = models.CharField(max_length=20, blank=True)
    # The job's name in the converter's inbox while it converts: random, like every token.
    preview_token = models.CharField(max_length=64, blank=True)
    preview_since = models.DateTimeField(null=True, blank=True)
    # A video's length. Its "pages" are its parts of PART_SECONDS each, so the
    # reading analytics count time watched on each part.
    seconds = models.FloatField(default=0)
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
    def office(self) -> OfficeFormat | None:
        """How to name and serve this version, if it is a Word, PowerPoint or Excel file."""
        return OFFICE.get(self.kind)

    @property
    def is_office(self) -> bool:
        return self.kind in OFFICE

    @property
    def reads_as_pages(self) -> bool:
        """Read page by page: a PDF, or an Office file whose preview is ready.

        An Office file's preview is shown only while the converter is
        installed and previews are on in Settings; otherwise the file is a
        download card again.
        """
        if self.kind == self.Kind.PDF:
            return True
        if not (self.is_office and self.preview == self.Preview.READY and settings.CONFIG.office):
            return False
        from snug.core.models import SiteSettings

        return SiteSettings.load().office_previews

    @property
    def is_workbook(self) -> bool:
        return self.kind == self.Kind.XLSX

    @property
    def is_video(self) -> bool:
        return self.kind == self.Kind.MP4

    @property
    def length(self) -> str:
        """A video's length as m:ss, or h:mm:ss."""
        whole = round(self.seconds)
        hours, rest = divmod(whole, 3600)
        minutes, seconds = divmod(rest, 60)
        return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"

    @property
    def describe(self) -> str:
        """For admins and investors: "12 pages", "Excel workbook, 3 sheets" or "MP4 video, 2:05"."""
        if self.is_video:
            return f"MP4 video, {self.length}"
        if self.is_workbook:
            count = len(self.sheets)
            return f"Excel workbook, {count} sheet{'' if count == 1 else 's'}"
        if self.kind == self.Kind.PPTX:
            return f"PowerPoint presentation, {self.slides} slide{'' if self.slides == 1 else 's'}"
        if self.kind == self.Kind.DOCX:
            return "Word document"
        return f"{self.page_count} page{'' if self.page_count == 1 else 's'}"
