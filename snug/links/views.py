"""The admin's link pages: create, show once, list, revoke, reissue and extend."""

import base64
import binascii
import re
import secrets
from datetime import date, datetime, time, timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Q
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
from snug.links.freemail import FREE_MAIL
from snug.links.models import (
    MAX_MESSAGE,
    Link,
    LinkPerson,
    is_domain,
    plain_address,
    plain_domain,
)
from snug.nda import terms as nda_terms
from snug.office import previews
from snug.rooms.models import Room

ISSUED = "snug.issued_link"
ISSUED_COOKIE = f"{'__Host-' if settings.CONFIG.https else ''}snug-issued"
ISSUED_SECONDS = 300
# The Links page's status filter: value and label.
STATUSES = (("active", "Active"), ("expired", "Expired"), ("revoked", "Revoked"))


def _admin(request: HttpRequest) -> Admin:
    user = request.user
    assert isinstance(user, Admin)  # noqa: S101 - every view here requires a signed-in admin
    return user


def _audit(request: HttpRequest, action: str, link: Link, **details: Any) -> None:
    record(
        action,
        actor_type=AuditEvent.ActorType.ADMIN,
        actor=_admin(request).email,
        target=link.recipient[:254],
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
    # A domain has no one address to send to: the founder copies the link.
    if not wanted or not any(not is_domain(entry) for entry in issued.link.entry_list):
        return None
    sent = service.send_link_email(issued, _admin(request))
    _audit(request, "link.emailed" if sent else "link.email_failed", issued.link)
    return sent


_SEPARATORS = re.compile(r"[\s,;]+")


def parse_entries(raw: str) -> tuple[list[str], str]:
    """Who a link is for, from the form: addresses and domains, and an error or ""."""
    entries: list[str] = []
    for item in _SEPARATORS.split(raw.strip()):
        if not item:
            continue
        entry = item.casefold()
        if "@" in entry:
            if not plain_address(entry):
                return [], WHO_ERROR
        elif not plain_domain(entry):
            return [], WHO_ERROR
        elif entry in FREE_MAIL:
            return [], (
                f"{entry} is a free email service, so a link for it would open for anyone. "
                "Enter the addresses instead."
            )
        if entry not in entries:
            entries.append(entry)
    if not entries:
        return [], WHO_ERROR
    return entries, ""


WHO_ERROR = "Enter email addresses or domains, such as priya@northfield.com or northfield.com."


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
        return "room_pages" if previews.shown() else "room"
    latest = document.latest_version() if document is not None else None
    if latest is not None and latest.is_video:
        return "video"
    if latest is not None and latest.is_office and latest.reads_as_pages:
        return "office_pages"
    if latest is not None and latest.is_workbook:
        return "workbook"
    return "office" if latest is not None and latest.is_office else "pdf"


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

    who = request.POST.get("email", "")
    entries, who_error = parse_entries(who)
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
        "email": who[:2000],
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
    if who_error:
        context["who_error"] = who_error
    elif (len(entries) > 1 or any(is_domain(entry) for entry in entries)) and not (
        require_verification
    ):
        error = (
            "A link for more than one person, or for a domain, needs email verification, "
            "so Snug knows who is reading. Tick Require email verification."
        )
    problem = nda_terms.first_problem(nda_text) if write_nda else ""
    if problem:
        context["nda_error"] = (
            "Write the NDA, or untick Ask for the NDA first."
            if problem == "missing"
            else nda_terms.TOO_LONG
        )
    if error or problem or who_error:
        return render(request, "links/new.html", {**context, "error": error}, status=400)
    if write_nda:
        nda_terms.publish_first(request, nda_text, by=_admin(request), written_on="link form")

    try:
        issued = service.create(
            document=document,
            room=room,
            entries=entries,
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
    now = timezone.now()
    status = request.GET.get("status", "")
    live = Q(expires_at__isnull=True) | Q(expires_at__gt=now)
    if status == "active":
        links = links.filter(live, revoked_at__isnull=True)
    elif status == "expired":
        links = links.filter(revoked_at__isnull=True, expires_at__lte=now)
    elif status == "revoked":
        links = links.filter(revoked_at__isnull=False)
    else:
        status = ""
    query = " ".join(request.GET.get("q", "").split())[:254]
    if query:
        ref = query.upper().removeprefix("L-")
        links = links.filter(
            Q(entries__icontains=query)
            | Q(note__icontains=query)
            | Q(ref=ref)
            | Q(document__title__icontains=query)
            | Q(room__name__icontains=query)
        )
    context = {
        "tab": "links",
        "links": links,
        "status": status,
        "q": query,
        "statuses": STATUSES,
        "filtered": bool(status or query),
    }
    return render(request, "links/list.html", context)


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


def _long_date(day: date) -> str:
    return f"{day.day} {day:%B %Y}"


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def link_extend(request: HttpRequest, ref: str) -> HttpResponse:
    link = get_object_or_404(Link.objects.select_related("document", "room"), ref=ref)
    last = link.last_day
    # A revoked link stays revoked, and one with no expiry has nothing to extend.
    if link.is_revoked() or last is None or link.expires_at is None:
        return redirect(link.target_url())
    current = link.expires_at
    # The first day the form accepts: after today, and after the link's last day.
    earliest = max(timezone.localdate(), last) + timedelta(days=1)
    context: dict[str, Any] = {
        "tab": "links",
        "link": link,
        "last_day": _long_date(last),
        "earliest": earliest.isoformat(),
        "value": (earliest + timedelta(days=13)).isoformat(),
    }
    if request.method == "GET":
        return render(request, "links/extend.html", context)

    raw = request.POST.get("expires", "").strip()
    expires_at, error = _parse_expiry(raw)
    if not error and expires_at is not None and expires_at <= current:
        error = f"Choose a day after {_long_date(last)}, its last day now."
    if error:
        context.update(value=raw[:40], error=error)
        return render(request, "links/extend.html", context, status=400)

    service.extend(link, expires_at)
    until = link.last_day
    _audit(
        request,
        "link.extended",
        link,
        **{"from": last.isoformat(), "to": until.isoformat() if until else None},
    )
    after = f"it now works until {_long_date(until)}" if until else "it now has no expiry"
    text = (
        f"Extended {link.label} for {link.recipient}, and {after}. "
        "Its address is unchanged, so there is nothing new to send."
    )
    return notices.saved(request, link.target_url(), "links", text)


# ---------------------------------------------------------------- a link's page, and its people


@never_cache
@login_required
@require_safe
def link_detail(request: HttpRequest, ref: str) -> HttpResponse:
    """Who a link is for, and each person on it, with blocking for a link to more than one."""
    link = get_object_or_404(Link.objects.select_related("document", "room"), ref=ref)
    context = {
        "tab": "links",
        "link": link,
        "people": link.people.select_related("blocked_by"),
        "notice": notices.take(request),
    }
    return render(request, "links/detail.html", context)


def _person(request: HttpRequest, link: Link) -> tuple[str, str]:
    """The address in the request, and why it cannot be blocked or unblocked, or ""."""
    source = request.POST if request.method == "POST" else request.GET
    address = plain_address(source.get("email", "")[:254])
    if link.for_one_person:
        return address, "This link is for one person. To stop it, revoke it."
    if not address or not link.listed(address):
        return address, "That address cannot open this link anyway."
    return address, ""


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def link_block(request: HttpRequest, ref: str) -> HttpResponse:
    link = get_object_or_404(Link.objects.select_related("document", "room"), ref=ref)
    address, refused = _person(request, link)
    context = {"tab": "links", "link": link, "email": address, "error": refused}
    if request.method == "GET" or refused:
        return render(request, "links/block.html", context, status=400 if refused else 200)
    if service.block(link, address, _admin(request)):
        _audit_person(request, "link.person_blocked", link, address)
    text = f"Blocked {address}. They can no longer open {link.label}, including pages already open."
    return notices.saved(request, reverse("links:detail", kwargs={"ref": link.ref}), "people", text)


@never_cache
@login_required
@require_http_methods(["POST"])
def link_unblock(request: HttpRequest, ref: str) -> HttpResponse:
    link = get_object_or_404(Link.objects.select_related("document", "room"), ref=ref)
    address, refused = _person(request, link)
    if refused:
        context = {
            "tab": "links",
            "link": link,
            "email": address,
            "error": refused,
            "unblock": True,
        }
        return render(request, "links/block.html", context, status=400)
    unblocked = LinkPerson.objects.filter(
        link=link, email=address, blocked_at__isnull=False
    ).update(blocked_at=None, blocked_by=None)
    if unblocked:
        _audit_person(request, "link.person_unblocked", link, address)
    text = f"Unblocked {address}. They can open {link.label} again, with a new code."
    return notices.saved(request, reverse("links:detail", kwargs={"ref": link.ref}), "people", text)


def _audit_person(request: HttpRequest, action: str, link: Link, email: str) -> None:
    record(
        action,
        actor_type=AuditEvent.ActorType.ADMIN,
        actor=_admin(request).email,
        target=email,
        ip_address=client_ip(request),
        user_agent=user_agent(request),
        details={"link": link.label, **link.target_details()},
    )
