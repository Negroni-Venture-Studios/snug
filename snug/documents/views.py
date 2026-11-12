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
from snug.documents.ingest import Checked, UploadRejectedError, check, store
from snug.documents.models import MAX_DESCRIPTION, Document, DocumentVersion
from snug.office import previews
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
    request: HttpRequest, document: Document, checked: Checked, upload: UploadedFile[bytes]
) -> DocumentVersion:
    version = store(document, checked, filename=upload.name or "", uploaded_by=_admin(request))
    if version.preview == DocumentVersion.Preview.PENDING:
        transaction.on_commit(previews.drive_in_background)
    _audit(
        request,
        "document.version_uploaded",
        document.title,
        document=document.token,
        version=version.number,
        kind=version.kind,
        **_upload_details(version),
        sha256=version.sha256[:16],
    )
    return version


def _upload_details(version: DocumentVersion) -> dict[str, Any]:
    """What the audit log records about an upload's contents."""
    if version.is_workbook:
        return {"sheets": len(version.sheets)}
    if version.kind == DocumentVersion.Kind.PPTX:
        return {"slides": version.slides}
    if version.is_office:
        return {}
    if version.is_video:
        return {"seconds": version.seconds}
    return {"pages": version.page_count}


def _uploaded_what(version: DocumentVersion | None) -> str:
    """For the notice after an upload: "an Excel workbook with 3 sheets", "12 pages"."""
    if version is None:
        return "0 pages"
    if version.is_video:
        return f"an MP4 video, {version.length} long"
    if version.is_workbook:
        count = len(version.sheets)
        return f"an Excel workbook with {count} sheet{pluralize(count)}"
    if version.kind == DocumentVersion.Kind.PPTX:
        return f"a PowerPoint presentation with {version.slides} slide{pluralize(version.slides)}"
    if version.office is not None:
        return version.office.a_name
    return f"{version.page_count} page{pluralize(version.page_count)}"


CHOOSE = "Choose a PDF, a Word, PowerPoint or Excel file, or an MP4 video to upload."


@never_cache
@login_required
@require_safe
def document_list(request: HttpRequest) -> HttpResponse:
    ready = DocumentVersion.objects.filter(status=DocumentVersion.Status.READY).order_by("-number")
    documents = Document.objects.prefetch_related(Prefetch("versions", queryset=ready))
    rows = [(document, next(iter(document.versions.all()), None)) for document in documents]
    making = set(
        DocumentVersion.objects.filter(
            preview__in=(DocumentVersion.Preview.PENDING, DocumentVersion.Preview.CONVERTING)
        ).values_list("document_id", flat=True)
    )
    context = {"tab": "documents", "rows": rows, "making": making}
    return render(request, "documents/list.html", context)


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
        context["error"] = CHOOSE
    if "error" in context or upload is None:
        return render(request, "documents/new.html", context, status=400)

    # Checked before the write transaction, which would otherwise hold every
    # other writer back while the worker reads the file.
    try:
        checked = check(upload.chunks())
    except UploadRejectedError as error:
        _audit(request, "document.upload_refused", title, reason=str(error))
        context["error"] = str(error)
        return render(request, "documents/new.html", context, status=400)
    try:
        with transaction.atomic():
            document = Document.objects.create(title=title, created_by=_admin(request))
            _audit(request, "document.created", title, document=document.token)
            _store(request, document, checked, upload)
    finally:
        checked.discard()
    what = _uploaded_what(document.latest_version())
    return notices.saved(
        request,
        reverse("documents:detail", kwargs={"token": document.token}),
        "links",
        f"Uploaded, {what}. Create a link to share it.{_preview_note(document)}",
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


def _preview_panel(request: HttpRequest, document: Document) -> dict[str, Any] | None:
    """The Preview panel's state for an Office file while the converter is installed.

    It follows a version being prepared, if there is one, or else the latest.
    """
    preparing = document.versions.filter(status=DocumentVersion.Status.PREPARING).first()
    latest = document.latest_version()
    subject = preparing or latest
    if subject is None or not subject.is_office or not previews.enabled():
        return None
    if subject.preview == DocumentVersion.Preview.NONE:
        return None
    waiting = subject.preview in (
        DocumentVersion.Preview.PENDING,
        DocumentVersion.Preview.CONVERTING,
    )
    panel: dict[str, Any] = {
        "version": subject,
        "waiting": waiting,
        "stage": previews.stage(subject) if waiting else "",
        "failed": subject.preview == DocumentVersion.Preview.FAILED,
        "message": previews.message(subject.preview_error),
        "ready": subject.preview == DocumentVersion.Preview.READY,
        "preparing": preparing is not None,
        "showing": latest,
        "poll_url": reverse("documents:preview_status", kwargs={"token": document.token}),
        "retry_url": reverse(
            "documents:preview_retry", kwargs={"token": document.token, "number": subject.number}
        ),
    }
    if panel["ready"] and subject.page_sizes:
        width, height = subject.page_sizes[0]
        panel["size"] = {"width": PREVIEW_WIDTH, "height": round(PREVIEW_WIDTH * height / width)}
    return panel


def _detail_context(request: HttpRequest, document: Document) -> dict[str, Any]:
    # A version being prepared is listed too; links keep showing the latest ready one.
    shown = (DocumentVersion.Status.READY, DocumentVersion.Status.PREPARING)
    versions = list(document.versions.filter(status__in=shown))
    latest = document.latest_version()
    preview: dict[str, Any] | None = None
    if latest and latest.page_sizes:
        width, height = latest.page_sizes[0]
        preview = {"width": PREVIEW_WIDTH, "height": round(PREVIEW_WIDTH * height / width)}
    return {
        "tab": "documents",
        "document": document,
        # An Office file is downloaded, not read page by page: it has no reading chart.
        "reading": None
        if latest and not latest.reads_as_pages and latest.is_office
        else _reading(document, video=bool(latest and latest.is_video)),
        "links": document.links.all(),
        "versions": versions,
        "latest": latest,
        "preview": preview,
        "panel": _preview_panel(request, document),
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
        error = CHOOSE
    else:
        try:
            checked = check(upload.chunks(), document=document)
        except UploadRejectedError as rejected:
            _audit(request, "document.upload_refused", document.title, reason=str(rejected))
            error = str(rejected)
        else:
            _store(request, document, checked, upload)
    if error:
        context = {**_detail_context(request, document), "error": error}
        return render(request, "documents/detail.html", context, status=400)
    version = document.latest_version()
    number = version.number if version else 0
    return notices.saved(
        request,
        reverse("documents:detail", kwargs={"token": document.token}),
        "versions",
        _version_notice(document, number),
    )


@never_cache
@login_required
@require_POST
def description(request: HttpRequest, token: str) -> HttpResponse:
    """An Office file's description, shown to investors on its card."""
    document = get_object_or_404(Document, token=token)
    latest = document.latest_version()
    if latest is None or not latest.is_office:
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
    where = (
        "beside the pages"
        if latest.reads_as_pages
        else "above the list of sheets"
        if latest.is_workbook
        else "above the download button"
    )
    said = f"Description saved. Investors see it {where}." if text else "Description removed."
    return notices.saved(
        request, reverse("documents:detail", kwargs={"token": document.token}), "description", said
    )


def _preview_note(document: Document) -> str:
    latest = document.latest_version()
    pending = latest is not None and latest.preview == DocumentVersion.Preview.PENDING
    return " Its preview is being made." if pending else ""


def _version_notice(document: Document, number: int) -> str:
    preparing = document.versions.filter(
        status=DocumentVersion.Status.PREPARING, number=number
    ).exists()
    if preparing:
        latest = document.latest_version()
        before = latest.number if latest else number - 1
        return (
            f"Version {number} uploaded. Links show it once its preview is ready, "
            f"and version {before} until then."
        )
    return f"Version {number} uploaded. Every link to this document shows it now."


@never_cache
@login_required
@require_safe
def preview_status(request: HttpRequest, token: str) -> HttpResponse:
    """The Preview panel alone, which admin.js asks for while the converter works.

    Asking moves the converter's queue on a step, so a watched preview
    arrives as soon as it is made.
    """
    document = get_object_or_404(Document, token=token)
    previews.pump()
    context = {"document": document, "panel": _preview_panel(request, document)}
    return render(request, "documents/partials/preview_panel.html", context)


@never_cache
@login_required
@require_POST
def preview_retry(request: HttpRequest, token: str, number: int) -> HttpResponse:
    """Queue a failed preview again."""
    version = get_object_or_404(
        DocumentVersion,
        document__token=token,
        number=number,
        preview=DocumentVersion.Preview.FAILED,
    )
    if not previews.enabled():
        raise Http404
    version.preview, version.preview_error = DocumentVersion.Preview.PENDING, ""
    version.save(update_fields=["preview", "preview_error"])
    _audit(
        request,
        "document.preview_retried",
        version.document.title,
        document=token,
        version=number,
    )
    transaction.on_commit(previews.drive_in_background)
    return notices.saved(
        request,
        reverse("documents:detail", kwargs={"token": token}),
        "preview",
        "Trying again. The preview appears here when it is ready.",
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
    )
    if not version.reads_as_pages:
        raise Http404
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
