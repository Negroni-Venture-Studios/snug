"""The admin's documents: list, upload, versions and page previews."""

# UploadedFile is generic in the type stubs only, so annotations must not be
# evaluated at runtime.
from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.db.models import Prefetch
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.shortcuts import get_object_or_404, render
from django.template.defaultfilters import pluralize
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from snug.accounts.models import Admin
from snug.analytics import charts, summary
from snug.audit.models import AuditEvent, record
from snug.core import notices, ranges
from snug.core.client import client_ip, user_agent
from snug.documents import storage
from snug.documents.ingest import UploadRejectedError, ingest
from snug.documents.models import MAX_DESCRIPTION, Document, DocumentVersion
from snug.rendering import pages, sandbox

MAX_TITLE = 200
PREVIEW_WIDTH = 800


def _admin(request: HttpRequest) -> Admin:
    user = request.user
    assert isinstance(user, Admin)  # noqa: S101 - every view here requires a signed-in admin
    return user


def _audit(request: HttpRequest, action: str, target: str, **details: Any) -> None:
    record(
        action,
        actor_type=AuditEvent.ActorType.ADMIN,
        actor=_admin(request).email,
        target=target[:254],
        ip_address=client_ip(request),
        user_agent=user_agent(request),
        details=details,
    )


def _limit_mb() -> int:
    limit: int = settings.MAX_UPLOAD_BYTES
    return limit // (1024 * 1024)


def _uploaded(request: HttpRequest) -> UploadedFile[bytes] | None:
    upload = request.FILES.get("pdf")
    return upload if isinstance(upload, UploadedFile) else None


def _store(
    request: HttpRequest, document: Document, upload: UploadedFile[bytes]
) -> DocumentVersion:
    version = ingest(
        document, upload.chunks(), filename=upload.name or "", uploaded_by=_admin(request)
    )
    _audit(
        request,
        "document.version_uploaded",
        document.title,
        document=document.token,
        version=version.number,
        kind=version.kind,
        **(
            {"sheets": len(version.sheets)}
            if version.is_workbook
            else {"seconds": version.seconds}
            if version.is_video
            else {"pages": version.page_count}
        ),
        sha256=version.sha256[:16],
    )
    return version


@never_cache
@login_required
@require_safe
def document_list(request: HttpRequest) -> HttpResponse:
    ready = DocumentVersion.objects.filter(status=DocumentVersion.Status.READY).order_by("-number")
    documents = Document.objects.prefetch_related(Prefetch("versions", queryset=ready))
    rows = [(document, next(iter(document.versions.all()), None)) for document in documents]
    return render(request, "documents/list.html", {"tab": "documents", "rows": rows})


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def document_new(request: HttpRequest) -> HttpResponse:
    context: dict[str, Any] = {
        "tab": "documents",
        "limit_mb": _limit_mb(),
        "video_mb": settings.MAX_VIDEO_BYTES // (1024 * 1024),
        "title": "",
    }
    if request.method == "GET":
        return render(request, "documents/new.html", context)

    title = " ".join(request.POST.get("title", "").split())[:MAX_TITLE]
    upload = _uploaded(request)
    context["title"] = title
    if not title:
        context["error"] = "Give the document a title."
    elif upload is None:
        context["error"] = "Choose a PDF, an Excel workbook or an MP4 video to upload."
    if "error" in context or upload is None:
        return render(request, "documents/new.html", context, status=400)

    try:
        with transaction.atomic():
            document = Document.objects.create(title=title, created_by=_admin(request))
            _audit(request, "document.created", title, document=document.token)
            _store(request, document, upload)
    except UploadRejectedError as error:
        _audit(request, "document.upload_refused", title, reason=str(error))
        context["error"] = str(error)
        return render(request, "documents/new.html", context, status=400)
    version = document.latest_version()
    if version is not None and version.is_video:
        what = f"an MP4 video, {version.length} long"
    elif version is not None and version.is_workbook:
        count = len(version.sheets)
        what = f"an Excel workbook with {count} sheet{pluralize(count)}"
    else:
        pages = version.page_count if version else 0
        what = f"{pages} page{pluralize(pages)}"
    return notices.saved(
        request,
        reverse("documents:detail", kwargs={"token": document.token}),
        "links",
        f"Uploaded, {what}. Create a link to share it.",
    )


def _reading(document: Document, *, video: bool = False) -> dict[str, Any] | None:
    """The stat strip and reading grid, ready for the template."""
    reading = summary.for_document(document)
    if reading is None:
        return None
    median = round(reading.median_seconds)
    return {
        "viewers": reading.viewer_count,
        "visits": reading.visits,
        "median_minutes": median // 60,
        "median_seconds": median % 60,
        "read_all": reading.read_all,
        "video": video,
        "axis": charts.axis(reading.pages, video=video),
        "bars": charts.bars(reading.averages, video=video),
        "legend": charts.legend(),
        "rows": [
            {
                "email": row.email,
                "note": row.note,
                "strip": charts.strip(row.seconds, row.seen, row.email, video=video),
                "visits": row.visits,
                "total": charts.duration(row.total_seconds),
                "last_seen": row.last_seen,
            }
            for row in reading.viewers
        ],
        "longest": " and ".join(
            f"{charts.label(page - 1, video=video) if video else f'page {page}'} "
            f"({charts.duration(avg)})"
            for page, avg in reading.longest()
        ),
    }


def _detail_context(request: HttpRequest, document: Document) -> dict[str, Any]:
    versions = list(document.versions.filter(status=DocumentVersion.Status.READY))
    latest = versions[0] if versions else None
    preview: dict[str, Any] | None = None
    if latest and latest.page_sizes:
        width, height = latest.page_sizes[0]
        preview = {"width": PREVIEW_WIDTH, "height": round(PREVIEW_WIDTH * height / width)}
    return {
        "tab": "documents",
        "document": document,
        # A workbook is downloaded, not read page by page: it has no reading chart.
        "reading": None
        if latest and latest.is_workbook
        else _reading(document, video=bool(latest and latest.is_video)),
        "links": document.links.all(),
        "versions": versions,
        "latest": latest,
        "preview": preview,
        "limit_mb": _limit_mb(),
        "video_mb": settings.MAX_VIDEO_BYTES // (1024 * 1024),
        "notice": notices.take(request),
    }


@never_cache
@login_required
@require_safe
def document_detail(request: HttpRequest, token: str) -> HttpResponse:
    document = get_object_or_404(Document, token=token)
    return render(request, "documents/detail.html", _detail_context(request, document))


@never_cache
@login_required
@require_POST
def version_upload(request: HttpRequest, token: str) -> HttpResponse:
    document = get_object_or_404(Document, token=token)
    upload = _uploaded(request)
    error = None
    if upload is None:
        error = "Choose a PDF, an Excel workbook or an MP4 video to upload."
    else:
        try:
            _store(request, document, upload)
        except UploadRejectedError as rejected:
            _audit(request, "document.upload_refused", document.title, reason=str(rejected))
            error = str(rejected)
    if error:
        context = {**_detail_context(request, document), "error": error}
        return render(request, "documents/detail.html", context, status=400)
    version = document.latest_version()
    number = version.number if version else 0
    return notices.saved(
        request,
        reverse("documents:detail", kwargs={"token": document.token}),
        "versions",
        f"Version {number} uploaded. Every link to this document shows it now.",
    )


@never_cache
@login_required
@require_POST
def description(request: HttpRequest, token: str) -> HttpResponse:
    """A workbook's description, shown to investors above its sheets."""
    document = get_object_or_404(Document, token=token)
    latest = document.latest_version()
    if latest is None or not latest.is_workbook:
        raise Http404
    text = request.POST.get("description", "").strip()
    if len(text) > MAX_DESCRIPTION:
        context = {
            **_detail_context(request, document),
            "description_error": "Keep the description to 1,000 characters or fewer.",
            "description_value": text[: MAX_DESCRIPTION * 2],
        }
        return render(request, "documents/detail.html", context, status=400)
    document.description = text
    document.save(update_fields=["description"])
    _audit(
        request,
        "document.description_changed",
        document.title,
        document=document.token,
        characters=len(text),
    )
    said = (
        "Description saved. Investors see it above the list of sheets."
        if text
        else "Description removed."
    )
    return notices.saved(
        request, reverse("documents:detail", kwargs={"token": document.token}), "description", said
    )


@login_required
@require_safe
def video_preview(request: HttpRequest, token: str, number: int) -> HttpResponseBase:
    """A version's video, for admins only, in the ranges the player asks for."""
    version = get_object_or_404(
        DocumentVersion,
        document__token=token,
        number=number,
        status=DocumentVersion.Status.READY,
        kind=DocumentVersion.Kind.MP4,
    )
    response = ranges.serve(request, storage.original_path(version), "video/mp4")
    response["Cache-Control"] = "private, max-age=300"
    return response


@login_required
@require_safe
def page_preview(
    request: HttpRequest, token: str, number: int, page: int
) -> HttpResponse | FileResponse:
    """An unwatermarked page, for admins only. Viewers will never reach this."""
    version = get_object_or_404(
        DocumentVersion,
        document__token=token,
        number=number,
        status=DocumentVersion.Status.READY,
        kind=DocumentVersion.Kind.PDF,
    )
    try:
        path = pages.page_image(version, page, width=PREVIEW_WIDTH)
    except ValueError as error:
        raise Http404 from error
    except sandbox.WorkerError:
        return HttpResponse(
            "This page could not be rendered.", status=503, content_type="text/plain"
        )
    response = FileResponse(path.open("rb"), content_type="image/webp")
    response["Cache-Control"] = "private, max-age=3600"
    return response
