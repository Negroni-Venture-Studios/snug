"""The viewer's pages: landing, code, one-click open, and the document.

Every view here is wrapped by one of two decorators, and a test walks the URL
configuration to make sure of it:

- viewer_content calls access.authorise, the single authorisation path, and
  is the only way to reach anything from a document;
- viewer_gate runs only its first check (the link is active), for the pages
  that exist to start a session.
"""

from collections.abc import Callable
from functools import wraps
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from snug.audit.models import AuditEvent, record
from snug.core import mail, ratelimit
from snug.core.client import client_ip, user_agent
from snug.links.models import Link
from snug.viewer import codes, limits, sessions
from snug.viewer.access import Access, NotVerified, Unavailable, authorise, find_link
from snug.viewer.models import VerificationCode

CHECK_ATTRIBUTE = "snug_viewer_check"
TOO_MANY = "Too many attempts. Wait 15 minutes, then try again."

GateView = Callable[[HttpRequest, Link, str], HttpResponse]


def _unavailable(request: HttpRequest) -> HttpResponse:
    return render(request, "viewer/unavailable.html", status=404)


def _see_other(token: str) -> HttpResponse:
    """Back to the link's own address, after a form, as a GET."""
    response = HttpResponseRedirect(reverse("viewer:entry", kwargs={"token": token}))
    response.status_code = 303
    return response


def viewer_gate(view: GateView) -> Callable[..., HttpResponse]:
    """For pages that start a session: the link must exist and be active."""

    @wraps(view)
    def wrapper(request: HttpRequest, token: str) -> HttpResponse:
        try:
            link = find_link(token)
        except Unavailable:
            return _unavailable(request)
        return view(request, link, token)

    setattr(wrapper, CHECK_ATTRIBUTE, "find_link")
    return wrapper


def viewer_content(
    *, not_verified: GateView | None = None
) -> Callable[[Callable[..., HttpResponse]], Callable[..., HttpResponse]]:
    """For everything else: authorise() must allow the request."""

    def decorate(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
        @wraps(view)
        def wrapper(request: HttpRequest, token: str, **kwargs: Any) -> HttpResponse:
            document_token = kwargs.pop("document_token", None)
            try:
                access = authorise(request, token, document_token=document_token)
            except NotVerified as denied:
                if not_verified is not None:
                    return not_verified(request, denied.link, token)
                return _see_other(token)
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
    record(
        action,
        actor_type=AuditEvent.ActorType.VIEWER,
        actor=actor[:254],
        target=link.email,
        ip_address=client_ip(request),
        user_agent=user_agent(request),
        details={"link": link.label, "document": link.document.token, **details},
    )


def _typed_email(request: HttpRequest) -> str:
    return request.POST.get("email", "").strip()[:254]


def _matches(typed: str, link: Link) -> bool:
    return typed.casefold() == link.email.casefold()


def _context(link: Link, token: str, **extra: Any) -> dict[str, Any]:
    version = link.document.latest_version()
    return {
        "link": link,
        "token": token,
        "document": link.document,
        "pages": version.page_count if version else None,
        "company": settings.CONFIG.company_name,
        "email_enabled": mail.enabled(),
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


def _send(link: Link, code: str) -> bool:
    company = settings.CONFIG.company_name
    context = {
        "code": code,
        "document": link.document,
        "company": company,
        "minutes": codes.CODE_MINUTES,
    }
    return mail.send(
        to=link.email,
        subject=f"Your code for {company or link.document.title}: {code}",
        text=render_to_string("viewer/email/code.txt", context),
        html=render_to_string("viewer/email/code.html", context),
    )


def _send_refused(request: HttpRequest, link: Link, token: str, typed: str) -> HttpResponse:
    _audit(request, "viewer.code_refused", link, typed, reason="rate_limited")
    return _gate_page(request, link, token, error=TOO_MANY, typed=typed, status=429)


# ---------------------------------------------------------------- pages


@never_cache
@require_safe
@viewer_content(not_verified=_gate_page)
def entry(request: HttpRequest, access: Access, token: str) -> HttpResponse:
    # The reader replaces this page in step 4.
    return render(request, "viewer/document.html", _context(access.link, token))


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
    if not _send(link, codes.issue(link)):
        VerificationCode.objects.filter(link=link).delete()
        _audit(request, "viewer.code_failed_to_send", link, link.email)
        if neutral:
            return _code_page(request, link, token, neutral=True)
        error = "We could not send the code. Try again in a few minutes."
        return _gate_page(request, link, token, error=error, typed=typed, status=503)

    _audit(request, "viewer.code_sent", link, link.email)
    return _code_page(request, link, token, neutral=neutral)


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
