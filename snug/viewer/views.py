"""The viewer's pages: landing, code, one-click open, and the document.

Every view here is wrapped by one of two decorators, and a test walks the URL
configuration to make sure of it:

- viewer_content calls access.authorise, the single authorisation path, and
  is the only way to reach anything from a document;
- viewer_gate runs only its first check (the link is active), for the pages
  that exist to start a session.
"""

import hashlib
import threading
from collections.abc import Callable
from functools import wraps
from typing import Any

from django.db import connections
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.http.response import HttpResponseBase
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from snug.analytics import notify, reading
from snug.audit.models import AuditEvent, record
from snug.core import geoip, mail, ranges, ratelimit
from snug.core.client import client_ip, user_agent
from snug.core.models import SiteSettings, company_name
from snug.documents import storage
from snug.documents.models import PART_SECONDS, DocumentVersion
from snug.documents.storage import RENDER_WIDTHS
from snug.links.models import Link
from snug.nda import terms
from snug.rendering import pages as rendering
from snug.rendering.sandbox import WorkerError
from snug.viewer import codes, limits, sessions
from snug.viewer.access import (
    Access,
    NeedsNda,
    NotVerified,
    OutsideUK,
    Unavailable,
    authorise,
    check_place,
    find_link,
)
from snug.viewer.models import VerificationCode

CHECK_ATTRIBUTE = "snug_viewer_check"
TOO_MANY = "Too many attempts. Wait 15 minutes, then try again."
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

GateView = Callable[[HttpRequest, Link, str], HttpResponse]


def _unavailable(request: HttpRequest) -> HttpResponse:
    return render(request, "viewer/unavailable.html", status=404)


def _see_other(token: str, name: str = "viewer:entry") -> HttpResponse:
    """Back to the link's own address (or another of its pages), as a GET."""
    response = HttpResponseRedirect(reverse(name, kwargs={"token": token}))
    response.status_code = 303
    return response


def _outside(request: HttpRequest, refused: OutsideUK) -> HttpResponse:
    """A UK-only link opened from elsewhere, or from somewhere nobody can place."""
    _audit(
        request,
        "viewer.outside_uk",
        refused.link,
        actor="",
        country=refused.country or "unknown",
    )
    return render(request, "viewer/outside.html", status=403)


def viewer_gate(view: GateView) -> Callable[..., HttpResponse]:
    """For pages that start a session: the link must exist and be active, and a
    UK-only link must be opened from the UK, so nobody abroad is sent a code."""

    @wraps(view)
    def wrapper(request: HttpRequest, token: str) -> HttpResponse:
        try:
            link = find_link(token)
            check_place(request, link)
        except Unavailable:
            return _unavailable(request)
        except OutsideUK as refused:
            return _outside(request, refused)
        return view(request, link, token)

    setattr(wrapper, CHECK_ATTRIBUTE, "find_link")
    return wrapper


def viewer_content(
    *, not_verified: GateView | None = None
) -> Callable[[Callable[..., HttpResponseBase]], Callable[..., HttpResponseBase]]:
    """For everything else: authorise() must allow the request."""

    def decorate(view: Callable[..., HttpResponseBase]) -> Callable[..., HttpResponseBase]:
        @wraps(view)
        def wrapper(request: HttpRequest, token: str, **kwargs: Any) -> HttpResponseBase:
            document_token = kwargs.pop("document_token", None)
            try:
                access = authorise(request, token, document_token=document_token)
            except NotVerified as denied:
                try:
                    check_place(request, denied.link)
                except OutsideUK as refused:
                    return _outside(request, refused)
                if not_verified is not None:
                    return not_verified(request, denied.link, token)
                return _see_other(token)
            except OutsideUK as refused:
                return _outside(request, refused)
            except NeedsNda:
                return _see_other(token, "viewer:nda")
            except Unavailable:
                return _unavailable(request)
            return view(request, access, token, **kwargs)

        setattr(wrapper, CHECK_ATTRIBUTE, "authorise")
        return wrapper

    return decorate


# ---------------------------------------------------------------- helpers


def _ip(request: HttpRequest) -> str:
    return client_ip(request) or "unknown"


def _audit(request: HttpRequest, action: str, link: Link, actor: str, **details: Any) -> None:
    """A viewer event, with the visitor's country whether or not the link is UK-only."""
    address = client_ip(request)
    record(
        action,
        actor_type=AuditEvent.ActorType.VIEWER,
        actor=actor[:254],
        target=link.email,
        ip_address=address,
        user_agent=user_agent(request),
        country=geoip.country(address or "") or "",
        details={"link": link.label, **link.target_details(), **details},
    )


def _typed_email(request: HttpRequest) -> str:
    return request.POST.get("email", "").strip()[:254]


def _matches(typed: str, link: Link) -> bool:
    return typed.casefold() == link.email.casefold()


def _context(link: Link, token: str, **extra: Any) -> dict[str, Any]:
    version = link.document.latest_version() if link.document is not None else None
    return {
        "link": link,
        "token": token,
        "title": link.target_title,
        "document": link.document,
        "room": link.room,
        "pages": version.page_count if version else None,
        "workbook": bool(version and version.is_workbook),
        "video": bool(version and version.is_video),
        "length": version.length if version and version.is_video else "",
        "room_documents": link.room.entries.count() if link.room is not None else None,
        "company": company_name(),
        "email_enabled": mail.enabled(),
        "privacy_url": SiteSettings.load().privacy_url,
        **extra,
    }


def _gate_page(
    request: HttpRequest,
    link: Link,
    token: str,
    *,
    error: str = "",
    typed: str = "",
    status: int = 200,
) -> HttpResponse:
    """The landing page for a browser with no session for this link."""
    template = "viewer/verify.html" if link.require_verification else "viewer/open.html"
    context = _context(link, token, error=error, typed=typed)
    return render(request, template, context, status=status)


def _code_page(
    request: HttpRequest,
    link: Link,
    token: str,
    *,
    error: str = "",
    spent: bool = False,
    neutral: bool = False,
    status: int = 200,
) -> HttpResponse:
    # Show the address only if this request carried it, so the page never
    # tells a stranger who the link was sent to. In neutral mode it is never
    # shown, and the resend form carries back whatever was typed.
    typed = _typed_email(request)
    shown = link.email if _matches(typed, link) and not neutral else ""
    context = _context(
        link,
        token,
        shown=shown,
        resend=typed if neutral else shown,
        error=error,
        spent=spent,
        neutral=neutral,
    )
    return render(request, "viewer/code.html", context, status=status)


def _in_background(task: Callable[[], object]) -> threading.Thread:
    """Run a task without making the reply wait for it.

    Sending records whether email works, so the thread closes the database
    connection it opens.
    """

    def run() -> None:
        try:
            task()
        finally:
            connections.close_all()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _send(link: Link, code: str, company: str, via: mail.Outgoing | None = None) -> bool:
    """Email the code. `company` and `via` are read by the caller: this may run on
    another thread, which must not touch the database."""
    context = {
        "code": code,
        "title": link.target_title,
        "company": company,
        "minutes": codes.CODE_MINUTES,
    }
    return mail.send(
        to=link.email,
        subject=f"Your code for {company or link.target_title}: {code}",
        text=render_to_string("viewer/email/code.txt", context),
        html=render_to_string("viewer/email/code.html", context),
        via=via,
    )


def _send_refused(request: HttpRequest, link: Link, token: str, typed: str) -> HttpResponse:
    _audit(request, "viewer.code_refused", link, typed, reason="rate_limited")
    return _gate_page(request, link, token, error=TOO_MANY, typed=typed, status=429)


# ---------------------------------------------------------------- pages


class _Urls:
    """The reader's own addresses: under /d/<document> for a room link's documents."""

    def __init__(self, access: Access, token: str) -> None:
        self.kwargs: dict[str, Any] = {"token": token}
        self.prefix = ""
        if access.link.room is not None and access.document is not None:
            self.kwargs["document_token"] = access.document.token
            self.prefix = "room_"

    def reader(self) -> str:
        name = "viewer:room_document" if self.prefix else "viewer:entry"
        return str(reverse(name, kwargs=self.kwargs))

    def image(self, page: int, width: int) -> str:
        return str(
            reverse(
                f"viewer:{self.prefix}page", kwargs={**self.kwargs, "page": page, "width": width}
            )
        )

    def download(self) -> str:
        return str(reverse(f"viewer:{self.prefix}download", kwargs=self.kwargs))

    def video(self) -> str:
        return str(reverse(f"viewer:{self.prefix}video", kwargs=self.kwargs))


def _room_index(
    request: HttpRequest, access: Access, token: str, context: dict[str, Any]
) -> HttpResponse:
    """A room link's first page: its documents, under their folders."""
    room = access.link.room
    assert room is not None  # noqa: S101 - only room links reach the index without a document
    entries = list(room.entries.select_related("document", "folder").order_by("document__title"))

    def listed(entry: Any) -> dict[str, Any]:
        version = entry.document.latest_version()
        return {
            "title": entry.document.title,
            "url": reverse(
                "viewer:room_document",
                kwargs={"token": token, "document_token": entry.document.token},
            ),
            "describe": version.describe if version else None,
            "updated": version.uploaded_at if version else None,
        }

    folders = list(room.folders.all())
    context.update(
        top=[listed(e) for e in entries if e.folder_id is None],
        folders=[(f.name, [listed(e) for e in entries if e.folder_id == f.pk]) for f in folders],
        empty=not entries,
    )
    return render(request, "viewer/room.html", context)


def _workbook_card(
    request: HttpRequest,
    access: Access,
    token: str,
    version: DocumentVersion,
    context: dict[str, Any],
) -> HttpResponse:
    """A spreadsheet: its sheets and a download, since the working file is what matters.

    The card counts as the workbook's one page, so time on it is measured like a PDF's.
    """
    assert access.document is not None  # noqa: S101 - only document pages reach the card
    visit, new = reading.page_shown(access, access.document, version, 1)
    if new:
        notify.first_open(request, access, access.document)
        _audit(
            request,
            "viewer.document_viewed",
            access.link,
            access.session.email,
            version=version.number,
            document=access.document.token,
        )
    urls = _Urls(access, token)
    context.update(
        version=version,
        download_url=urls.download() if access.link.allow_download else None,
        room_url=reverse("viewer:entry", kwargs={"token": token}) if access.link.room else None,
        visit=visit.token,
        beat_url=reverse("viewer:beat", kwargs={"token": token}),
        beat_seconds=reading.BEAT_SECONDS,
    )
    return render(request, "viewer/workbook.html", context)


def _video_player(
    request: HttpRequest,
    access: Access,
    token: str,
    version: DocumentVersion,
    context: dict[str, Any],
) -> HttpResponse:
    """A video, played in the browser with the viewer's watermark drawn over it."""
    assert access.document is not None  # noqa: S101 - only document pages reach the player
    visit, new = reading.page_shown(access, access.document, version, 1)
    if new:
        notify.first_open(request, access, access.document)
        _audit(
            request,
            "viewer.document_viewed",
            access.link,
            access.session.email,
            version=version.number,
            document=access.document.token,
        )
    urls = _Urls(access, token)
    today = timezone.localdate()
    shared = f"Shared with {access.session.email}"
    width, height = version.page_sizes[0] if version.page_sizes else (16, 9)
    context.update(
        version=version,
        stream_url=urls.video(),
        download_url=urls.download() if access.link.allow_download else None,
        room_url=reverse("viewer:entry", kwargs={"token": token}) if access.link.room else None,
        mark=(f"{shared}, {today.day} {today:%b %Y}, link {access.link.label}"),
        visit=visit.token,
        beat_url=reverse("viewer:beat", kwargs={"token": token}),
        beat_seconds=reading.BEAT_SECONDS,
        part_seconds=PART_SECONDS,
        width=width,
        height=height,
    )
    return render(request, "viewer/video.html", context)


def _page_number(raw: str, count: int) -> int:
    """The page asked for, or the first page for anything odd."""
    if raw.isdecimal() and 1 <= int(raw) <= count:
        return int(raw)
    return 1


def _footer_band(width: int) -> int:
    # As the worker draws it: see snug.rendering.worker.stamp.
    return max(28, round(width * 0.03))


@never_cache
@require_safe
@viewer_content(not_verified=_gate_page)
def entry(request: HttpRequest, access: Access, token: str) -> HttpResponse:
    """The reader: one page at a time, each a watermarked image. A room link's index."""
    context = _context(access.link, token)
    if access.document is None:
        return _room_index(request, access, token, context)
    context.update(document=access.document, title=access.document.title)
    urls = _Urls(access, token)
    version = access.document.latest_version()
    if version is None:
        return render(request, "viewer/reader.html", context)
    if version.is_workbook:
        return _workbook_card(request, access, token, version, context)
    if version.is_video:
        return _video_player(request, access, token, version, context)
    count = version.page_count
    number = _page_number(request.GET.get("page", ""), count)
    sizes = version.page_sizes
    # Every upload records its page sizes; a 16:9 page if one is somehow missing.
    page_width, page_height = sizes[number - 1] if len(sizes) >= number else (16, 9)
    big = max(RENDER_WIDTHS)

    image = urls.image

    visit, new = reading.page_shown(access, access.document, version, number)
    if new:
        notify.first_open(request, access, access.document)
        _audit(
            request,
            "viewer.document_viewed",
            access.link,
            access.session.email,
            version=version.number,
            document=access.document.token,
        )
    context.update(
        download_url=urls.download(),
        room_url=reverse("viewer:entry", kwargs={"token": token}) if access.link.room else None,
        visit=visit.token,
        beat_url=reverse("viewer:beat", kwargs={"token": token}),
        beat_seconds=reading.BEAT_SECONDS,
        here=urls.reader(),
        page=number,
        count=count,
        pages_list=range(1, count + 1),
        src=image(number, big),
        srcset=", ".join(f"{image(number, w)} {w}w" for w in RENDER_WIDTHS),
        width=big,
        height=round(big * page_height / page_width) + _footer_band(big),
        previous=number - 1 if number > 1 else None,
        next=number + 1 if number < count else None,
        prefetch=image(number + 1, big) if number < count else None,
    )
    return render(request, "viewer/reader.html", context)


@require_safe
@viewer_content()
def page_image(
    request: HttpRequest, access: Access, token: str, page: int, width: int
) -> HttpResponse:
    """One page, stamped for this viewer on every request."""
    if access.document is None:
        raise Http404
    version = access.document.latest_version()
    if version is None or version.kind != DocumentVersion.Kind.PDF or width not in RENDER_WIDTHS:
        raise Http404
    if not 1 <= page <= version.page_count:
        raise Http404
    today = timezone.localdate()
    try:
        data = rendering.stamped_page(
            version,
            page,
            width,
            email=access.session.email,
            date=f"{today.day} {today:%b %Y}",
            ref=access.link.ref,
        )
    except WorkerError:
        return HttpResponse("This page could not be shown.", status=503, content_type="text/plain")
    response = HttpResponse(data, content_type="image/webp")
    # Kept briefly, by this browser only. Revoking stops new pages at once.
    response["Cache-Control"] = "private, max-age=300"
    return response


@require_safe
@viewer_content()
def video_stream(request: HttpRequest, access: Access, token: str) -> HttpResponseBase:
    """The video, in the byte ranges the player asks for. Every request is authorised."""
    if access.document is None:
        raise Http404
    version = access.document.latest_version()
    if version is None or not version.is_video:
        raise Http404
    response = ranges.serve(request, storage.original_path(version), "video/mp4", ranges_only=True)
    # Kept briefly, by this browser only. Revoking stops new requests at once.
    response["Cache-Control"] = "private, max-age=300"
    return response


@require_POST
@viewer_content()
def beat(request: HttpRequest, access: Access, token: str) -> HttpResponse:
    """A heartbeat from the reader while its tab is visible: see snug.analytics.reading."""
    if not ratelimit.allow(limits.BEAT_PER_IP, _ip(request)):
        return HttpResponse(status=429)
    raw_page = request.POST.get("page", "")
    if not raw_page.isdecimal() or not reading.heartbeat(
        access, request.POST.get("visit", "")[:64], int(raw_page), request.POST.get("kind", "")
    ):
        return HttpResponse(status=400)
    return HttpResponse(status=204)


def viewer_nda(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """For the NDA page: authorise(), stopping before the NDA check it exists to pass."""

    @wraps(view)
    def wrapper(request: HttpRequest, token: str) -> HttpResponse:
        try:
            access = authorise(request, token, check_nda=False)
        except NotVerified:
            return _see_other(token)
        except Unavailable:
            return _unavailable(request)
        return view(request, access, token)

    setattr(wrapper, CHECK_ATTRIBUTE, "authorise")
    return wrapper


@never_cache
@require_http_methods(["GET", "POST"])
@viewer_nda
def nda(request: HttpRequest, access: Access, token: str) -> HttpResponse:
    """The NDA, to accept before anything the link shares is shown."""
    version = terms.current()
    if version is None or not terms.required(access.link) or terms.accepted(access.link, version):
        return _see_other(token)
    context = _context(access.link, token, nda=version, errors={}, name="")
    if request.method == "GET":
        return render(request, "viewer/nda.html", context)

    name = " ".join(request.POST.get("name", "").split())[:200]
    errors = {}
    if not name:
        errors["name"] = "Enter your name."
    if request.POST.get("accept") != "on":
        errors["accept"] = "Tick the box to accept."
    if errors:
        context.update(errors=errors, name=name)
        return render(request, "viewer/nda.html", context, status=400)
    terms.accept(
        access.link,
        version,
        name=name,
        email=access.session.email,
        ip=client_ip(request),
        user_agent=user_agent(request),
    )
    _audit(
        request,
        "viewer.nda_accepted",
        access.link,
        access.session.email,
        name=name,
        nda_version=version.number,
        sha256=version.sha256,
    )
    return _see_other(token)


def _filename(title: str, extension: str = ".pdf") -> str:
    """The document's title as a plain file name: letters, digits and spaces."""
    kept = "".join(c if c.isalnum() or c in " -_" else " " for c in title)
    return (" ".join(kept.split()) or "document")[:100] + extension


@never_cache
@require_safe
@viewer_content()
def download(request: HttpRequest, access: Access, token: str) -> HttpResponseBase:
    """The original PDF, stamped for this viewer, when the link allows downloads."""
    if access.document is None:
        raise Http404
    version = access.document.latest_version()
    if not access.link.allow_download or version is None:
        raise Http404
    if not ratelimit.allow(limits.DOWNLOAD_PER_LINK, str(access.link.pk)):
        return HttpResponse(TOO_MANY, status=429, content_type="text/plain")
    if version.is_video:
        # The original file: a video cannot carry a visible mark without re-encoding.
        _audit(
            request,
            "viewer.downloaded",
            access.link,
            access.session.email,
            document=access.document.token,
            version=version.number,
            sha256=version.sha256,
        )
        return FileResponse(
            storage.original_path(version).open("rb"),
            as_attachment=True,
            filename=_filename(access.document.title, ".mp4"),
            content_type="video/mp4",
        )
    today = timezone.localdate()
    stamp = {
        "email": access.session.email,
        "date": f"{today.day} {today:%b %Y}",
        "ref": access.link.ref,
    }
    try:
        if version.is_workbook:
            data = rendering.stamped_xlsx(version, **stamp)
        else:
            data = rendering.stamped_pdf(version, **stamp)
    except WorkerError:
        message = "This document could not be prepared for download. Try again in a few minutes."
        return HttpResponse(message, status=503, content_type="text/plain")
    _audit(
        request,
        "viewer.downloaded",
        access.link,
        access.session.email,
        document=access.document.token,
        version=version.number,
        sha256=hashlib.sha256(data).hexdigest(),
    )
    workbook = version.is_workbook
    response = HttpResponse(data, content_type=XLSX_TYPE if workbook else "application/pdf")
    response["Content-Disposition"] = content_disposition_header(
        True, _filename(access.document.title, ".xlsx" if workbook else ".pdf")
    )
    return response


@never_cache
@require_POST
@viewer_gate
def send_code(request: HttpRequest, link: Link, token: str) -> HttpResponse:
    typed = _typed_email(request)
    if not mail.enabled() or not link.require_verification:
        return _gate_page(request, link, token, typed=typed, status=503)
    if not ratelimit.allow(limits.SEND_PER_IP, _ip(request)):
        return _send_refused(request, link, token, typed)

    # Past the cap on wrong addresses, every request gets the same neutral
    # reply, so guessing learns nothing, and the right address still gets a
    # code, so nobody can lock the owner out.
    neutral = ratelimit.exceeded(limits.WRONG_EMAIL_PER_LINK, str(link.pk))
    if not _matches(typed, link):
        # Counted first, so racing guesses cannot all get the plain answer.
        over = not ratelimit.allow(limits.WRONG_EMAIL_PER_LINK, str(link.pk))
        _audit(request, "viewer.code_refused", link, typed, reason="wrong_email")
        if neutral or over:
            return _code_page(request, link, token, neutral=True)
        error = (
            "That is not the address this link was sent to. "
            "Check for a typo, or ask the person who sent it."
        )
        return _gate_page(request, link, token, error=error, typed=typed, status=400)

    if not ratelimit.allow(limits.SEND_PER_LINK, str(link.pk)) or not ratelimit.allow(
        limits.SEND_PER_EMAIL, link.email
    ):
        if neutral:
            _audit(request, "viewer.code_refused", link, typed, reason="rate_limited")
            return _code_page(request, link, token, neutral=True)
        return _send_refused(request, link, token, typed)
    if neutral:
        # Sent without waiting, so the reply takes as long as a wrong
        # address's would. A failure is logged by mail.send, not shown.
        code = codes.issue(link)
        company = company_name()
        via = mail.outgoing()
        _in_background(lambda: _send(link, code, company, via))
        _audit(request, "viewer.code_sent", link, link.email, background=True)
        return _code_page(request, link, token, neutral=True)
    if not _send(link, codes.issue(link), company_name()):
        VerificationCode.objects.filter(link=link).delete()
        _audit(request, "viewer.code_failed_to_send", link, link.email)
        error = "We could not send the code. Try again in a few minutes."
        return _gate_page(request, link, token, error=error, typed=typed, status=503)

    _audit(request, "viewer.code_sent", link, link.email)
    return _code_page(request, link, token)


@never_cache
@require_http_methods(["GET", "POST"])
@viewer_gate
def verify_code(request: HttpRequest, link: Link, token: str) -> HttpResponse:
    if request.method == "GET":
        return _see_other(token)
    if not ratelimit.allow(limits.CHECK_PER_IP, _ip(request)):
        _audit(request, "viewer.code_wrong", link, _typed_email(request), reason="rate_limited")
        return _code_page(request, link, token, error=TOO_MANY, status=429)

    result = codes.check(link, request.POST.get("code", "")[:20])
    if result.ok:
        response = _see_other(token)
        sessions.begin(request, response, link, verified=True)
        _audit(request, "viewer.verified", link, link.email)
        return response

    _audit(
        request,
        "viewer.code_wrong",
        link,
        _typed_email(request),
        tries_left=result.tries_left,
        limited=result.limited,
    )
    if ratelimit.exceeded(limits.WRONG_EMAIL_PER_LINK, str(link.pk)):
        # In neutral mode, "tries left" would mean a code was sent, and
        # "expired" or the daily cap that none was or one was: each answers a
        # guess about the address. So every wrong code reads the same.
        error = "That code is not right."
        return _code_page(request, link, token, error=error, neutral=True, status=400)
    if result.limited:
        error = "Too many attempts on this link today. Try again tomorrow."
        return _code_page(request, link, token, error=error, spent=True, status=429)
    if result.tries_left:
        tries = "1 try" if result.tries_left == 1 else f"{result.tries_left} tries"
        error = f"That code is not right. You have {tries} left."
        return _code_page(request, link, token, error=error, status=400)
    error = "That code has expired or been used up."
    return _code_page(request, link, token, error=error, spent=True, status=400)


@never_cache
@require_POST
@viewer_gate
def open_link(request: HttpRequest, link: Link, token: str) -> HttpResponse:
    response = _see_other(token)
    if link.require_verification:
        return response
    if not ratelimit.allow(limits.OPEN_PER_IP, _ip(request)):
        return _gate_page(request, link, token, error=TOO_MANY, status=429)
    sessions.begin(request, response, link, verified=False)
    _audit(request, "viewer.opened", link, link.email)
    return response
