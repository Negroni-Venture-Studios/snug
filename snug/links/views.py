"""The admin's link pages: create, show once, list, revoke and reissue."""

import base64
import binascii
import secrets
from datetime import date, datetime, time, timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_safe

from snug.accounts.models import Admin
from snug.audit.models import AuditEvent, record
from snug.core import mail, notices
from snug.core.client import client_ip, user_agent
from snug.documents.models import Document
from snug.links import service
from snug.links.models import MAX_MESSAGE, Link
from snug.nda import terms as nda_terms
from snug.rooms.models import Room

ISSUED = "snug.issued_link"
ISSUED_COOKIE = f"{'__Host-' if settings.CONFIG.https else ''}snug-issued"
ISSUED_SECONDS = 300


def _admin(request: HttpRequest) -> Admin:
    user = request.user
    assert isinstance(user, Admin)  # noqa: S101 - every view here requires a signed-in admin
    return user


def _audit(request: HttpRequest, action: str, link: Link, **details: Any) -> None:
    record(
        action,
        actor_type=AuditEvent.ActorType.ADMIN,
        actor=_admin(request).email,
        target=link.email,
        ip_address=client_ip(request),
        user_agent=user_agent(request),
        details={"link": link.label, **link.target_details(), **details},
    )


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b, strict=True))


def _show_once(
    request: HttpRequest, issued: service.Issued, *, emailed: bool | None
) -> HttpResponse:
    # The token is split so that neither half opens the link alone. The
    # session, which is in the database and so in backups, holds it masked
    # with a one-time pad; the browser holds the pad in a short-lived cookie.
    # The next page joins the halves, shows the link once and drops both.
    token = issued.token.encode()
    pad = secrets.token_bytes(len(token))
    request.session[ISSUED] = {
        "masked": base64.urlsafe_b64encode(_xor(token, pad)).decode(),
        "link": issued.link.pk,
        "emailed": emailed,
    }
    response = redirect("links:issued")
    response.set_cookie(
        ISSUED_COOKIE,
        base64.urlsafe_b64encode(pad).decode(),
        max_age=ISSUED_SECONDS,
        path="/",
        secure=settings.SESSION_COOKIE_SECURE,
        httponly=True,
        samesite="Strict",
    )
    return response


def _unmask(masked: str, pad: str) -> str | None:
    try:
        return _xor(base64.urlsafe_b64decode(masked), base64.urlsafe_b64decode(pad)).decode()
    except (binascii.Error, ValueError):  # a missing, stale or altered half
        return None


def _deliver(request: HttpRequest, issued: service.Issued, wanted: bool) -> bool | None:
    if not wanted:
        return None
    sent = service.send_link_email(issued, _admin(request))
    _audit(request, "link.emailed" if sent else "link.email_failed", issued.link)
    return sent


def _parse_expiry(raw: str) -> tuple[datetime | None, str | None]:
    if not raw:
        return None, None
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        return None, "Enter the expiry as a date."
    if day <= timezone.localdate():
        return None, "The expiry date must be in the future."
    # The link works until the end of that day, in the instance's time zone.
    end = datetime.combine(day + timedelta(days=1), time.min)
    return timezone.make_aware(end), None


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def link_new(request: HttpRequest, document_token: str) -> HttpResponse:
    document = get_object_or_404(Document, token=document_token)
    target = {
        "title": document.title,
        "url": reverse("documents:detail", kwargs={"token": document.token}),
        "action": reverse("links:new", kwargs={"document_token": document.token}),
        "section": "Documents",
        "section_url": reverse("documents:list"),
    }
    return _new_link(request, target, document=document)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def link_new_room(request: HttpRequest, room_token: str) -> HttpResponse:
    room = get_object_or_404(Room, token=room_token)
    target = {
        "title": room.name,
        "url": reverse("rooms:detail", kwargs={"token": room.token}),
        "action": reverse("links:new_room", kwargs={"room_token": room.token}),
        "section": "Rooms",
        "section_url": reverse("rooms:list"),
    }
    return _new_link(request, target, room=room, tab="rooms")


def _download_kind(document: Document | None, room: Room | None) -> str:
    if room is not None:
        return "room"
    latest = document.latest_version() if document is not None else None
    if latest is not None and latest.is_video:
        return "video"
    return "workbook" if latest is not None and latest.is_workbook else "pdf"


def _new_link(
    request: HttpRequest,
    target: dict[str, str],
    *,
    document: Document | None = None,
    room: Room | None = None,
    tab: str = "documents",
) -> HttpResponse:
    context: dict[str, Any] = {
        "tab": tab,
        "target": target,
        "for_room": room is not None,
        "nda": nda_terms.current(),
        "email_enabled": mail.enabled(),
        "values": {"require_verification": True, "allow_download": True},
        # What a download is, which depends on what is shared.
        "download_kind": _download_kind(document, room),
    }
    if request.method == "GET":
        return render(request, "links/new.html", context)

    email = Admin.objects.normalize_email(request.POST.get("email", ""))
    note = " ".join(request.POST.get("note", "").split())[:200]
    message = request.POST.get("message", "").strip()
    expires_at, expiry_error = _parse_expiry(request.POST.get("expires", "").strip())
    require_verification = request.POST.get("require_verification") == "on"
    require_nda = document is not None and request.POST.get("require_nda") == "on"
    allow_download = request.POST.get("allow_download") == "on"
    uk_only = document is not None and request.POST.get("uk_only") == "on"
    # With no NDA yet, the form offers a box to write version 1.
    nda_text = request.POST.get("nda_text", "")
    write_nda = require_nda and nda_terms.current() is None
    values = {
        "email": email,
        "note": note,
        "message": message[: MAX_MESSAGE * 2],
        "expires": request.POST.get("expires", ""),
        "require_verification": require_verification,
        "allow_download": allow_download,
        "uk_only": uk_only,
        "require_nda": require_nda,
        "nda_text": nda_text[: nda_terms.MAX_TEXT + 1000],
    }
    context["values"] = values
    error = expiry_error
    if len(message) > MAX_MESSAGE:
        error = "Keep the message to 1,000 characters or fewer."
    try:
        validate_email(email)
    except ValidationError:
        error = "Enter the email address of the person this link is for."
    problem = nda_terms.first_problem(nda_text) if write_nda else ""
    if problem:
        context["nda_error"] = (
            "Write the NDA, or untick Ask for the NDA first."
            if problem == "missing"
            else nda_terms.TOO_LONG
        )
    if error or problem:
        return render(request, "links/new.html", {**context, "error": error}, status=400)
    if write_nda:
        nda_terms.publish_first(request, nda_text, by=_admin(request), written_on="link form")

    try:
        issued = service.create(
            document=document,
            room=room,
            email=email,
            note=note,
            message=message,
            expires_at=expires_at,
            require_verification=require_verification,
            require_nda=require_nda,
            allow_download=allow_download,
            uk_only=uk_only,
            created_by=_admin(request),
        )
    except service.NoFreeReferenceError:
        # Any NDA written on this form is published already, so the form shows it.
        context["nda"] = nda_terms.current()
        error = "Snug could not find a free link reference. Try again."
        return render(request, "links/new.html", {**context, "error": error}, status=503)
    _audit(
        request,
        "link.created",
        issued.link,
        verification=issued.link.require_verification,
        download=issued.link.allow_download,
        uk_only=issued.link.uk_only,
        expires=issued.link.expires_at.isoformat() if issued.link.expires_at else None,
    )
    wanted = request.POST.get("action") == "email" and mail.enabled()
    return _show_once(request, issued, emailed=_deliver(request, issued, wanted))


@never_cache
@login_required
@require_safe
def link_issued(request: HttpRequest) -> HttpResponse:
    issued = request.session.pop(ISSUED, None)
    pad = request.COOKIES.get(ISSUED_COOKIE)
    # Anything but the whole payload _show_once saved shows nothing.
    complete = isinstance(issued, dict) and isinstance(issued.get("masked"), str)
    if not (complete and "link" in issued and "emailed" in issued):
        issued = None
    token = _unmask(issued["masked"], pad) if issued and pad else None
    response: HttpResponse
    if token is None:
        response = redirect("links:list")
    else:
        link = get_object_or_404(Link, pk=issued["link"])
        context = {
            "tab": "links",
            "link": link,
            "url": service.link_url(token),
            "emailed": issued["emailed"],
        }
        response = render(request, "links/issued.html", context)
    if pad is not None:
        response.delete_cookie(ISSUED_COOKIE, path="/", samesite="Strict")
    return response


@never_cache
@login_required
@require_safe
def link_list(request: HttpRequest) -> HttpResponse:
    links = Link.objects.select_related("document", "room")
    return render(request, "links/list.html", {"tab": "links", "links": links})


def _confirm(request: HttpRequest, ref: str, action: str) -> tuple[Link, HttpResponse | None]:
    link = get_object_or_404(Link.objects.select_related("document", "room"), ref=ref)
    if request.method == "GET":
        context = {"tab": "links", "link": link, "action": action, "email_enabled": mail.enabled()}
        return link, render(request, "links/confirm.html", context)
    return link, None


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def link_revoke(request: HttpRequest, ref: str) -> HttpResponse:
    link, page = _confirm(request, ref, "revoke")
    if page:
        return page
    if not link.is_revoked():
        service.revoke(link, _admin(request))
        _audit(request, "link.revoked", link)
    text = f"Revoked {link.label}. It stopped working at once, including on pages already open."
    return notices.saved(request, link.target_url(), "links", text)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def link_reissue(request: HttpRequest, ref: str) -> HttpResponse:
    link, page = _confirm(request, ref, "reissue")
    if page:
        return page
    if link.is_revoked():
        return redirect(link.target_url())
    issued = service.reissue(link)
    _audit(request, "link.reissued", link)
    wanted = request.POST.get("action") == "email" and mail.enabled()
    return _show_once(request, issued, emailed=_deliver(request, issued, wanted))
