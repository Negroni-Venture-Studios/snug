"""Signing in and out: email and password, then a code from the app.

The password step never signs anyone in. It only records, in a fresh
session, that this admin may now prove the second factor, for five minutes.
"""

from typing import Any

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from snug.accounts import limits, passwords, recovery, sessions, totp
from snug.accounts.models import Admin
from snug.audit.models import AuditEvent, record
from snug.core import mail, ratelimit
from snug.core.client import client_ip, user_agent
from snug.core.models import company_name

TOO_MANY = "Too many attempts. Wait 15 minutes, then try again."
# An authenticator's code changes every 30 seconds; a recovery code never does.
WRONG_CODE = {
    "totp": (
        "That code is not right. Codes change every 30 seconds, "
        "so enter the one your app shows now."
    ),
    "recovery": "That code is not right.",
}


def _audit(
    request: HttpRequest, action: str, target: str, *, actor: str = "", **details: Any
) -> None:
    """Record a sign-in event. The actor is set only once someone is signed in."""
    record(
        action,
        actor_type=AuditEvent.ActorType.ADMIN,
        actor=actor,
        target=target[:254],
        ip_address=client_ip(request),
        user_agent=user_agent(request),
        details=details,
    )


def _safe_next(request: HttpRequest, candidate: str) -> str:
    """Only ever redirect to a path on this site after signing in."""
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return reverse("core:overview")


def _identity(request: HttpRequest) -> str:
    return client_ip(request) or "unknown"


@never_cache
@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("core:overview")
    context: dict[str, Any] = {
        "next": request.GET.get("next", ""),
        "email": "",
        "company": company_name(),
    }
    if request.method == "GET":
        return render(request, "accounts/login.html", context)

    email = Admin.objects.normalize_email(request.POST.get("email", ""))
    password = request.POST.get("password", "")
    context.update(email=email, next=request.POST.get("next", ""))
    ip = _identity(request)

    # Count, then decide: simultaneous requests cannot all slip under.
    if not ratelimit.allow(limits.PASSWORD_PER_IP, ip) or not ratelimit.allow(
        limits.PASSWORD_PER_ACCOUNT, email
    ):
        _audit(request, "admin.login_failed", email, step="password", reason="rate_limited")
        return render(request, "accounts/login.html", {**context, "error": TOO_MANY}, status=429)

    # authenticate() hashes a dummy password for unknown addresses, so a wrong
    # address takes as long to refuse as a wrong password.
    admin = authenticate(request, username=email, password=password)
    if not isinstance(admin, Admin) or admin.totp_confirmed_at is None:
        _audit(request, "admin.login_failed", email, step="password", reason="wrong")
        error = "That email address and password do not match an admin."
        return render(request, "accounts/login.html", {**context, "error": error})

    sessions.start_pending(request, admin, _safe_next(request, context["next"]))
    return redirect("accounts:login_code")


def _second_step(
    request: HttpRequest,
    *,
    template: str,
    per_ip: ratelimit.Limit,
    per_admin: ratelimit.Limit,
    method: str,
) -> HttpResponse:
    started = sessions.PENDING in request.session
    pending = sessions.get_pending(request)
    if pending is None:
        # Past the five minutes after the password, say so; otherwise there
        # was no password step at all.
        return redirect(reverse("accounts:login") + ("?expired=1" if started else ""))
    if request.method == "GET":
        return render(request, template)

    admin = Admin.objects.filter(pk=pending["admin_id"], is_active=True).first()
    if admin is None:
        sessions.clear_pending(request)
        return redirect("accounts:login")

    ip = _identity(request)
    if not ratelimit.allow(per_ip, ip) or not ratelimit.allow(per_admin, str(admin.pk)):
        _audit(request, "admin.login_failed", admin.email, step=method, reason="rate_limited")
        return render(request, template, {"error": TOO_MANY}, status=429)

    submitted = request.POST.get("code", "")
    with transaction.atomic():
        # Re-read inside the write transaction, so two requests carrying the
        # same code cannot both pass the replay check, and a password or
        # authenticator changed since the password step is seen.
        fresh = Admin.objects.filter(pk=admin.pk, is_active=True).first()
        current = fresh is not None and sessions.credentials_unchanged(pending, fresh)
        if fresh is None or not current:
            ok = False
        elif method == "totp":
            step = totp.verify(fresh.totp_secret, submitted, last_step=fresh.totp_last_step)
            if step is not None:
                fresh.totp_last_step = step
                fresh.save(update_fields=["totp_last_step"])
            ok = step is not None
        else:
            ok = recovery.use(fresh, submitted)

    if fresh is None or not current:
        sessions.clear_pending(request)
        _audit(
            request, "admin.login_failed", admin.email, step=method, reason="credentials_changed"
        )
        return redirect("accounts:login")
    if not ok:
        _audit(request, "admin.login_failed", admin.email, step=method, reason="wrong")
        if sessions.record_code_failure(request, pending) >= limits.CODE_FAILURES_PER_PASSWORD:
            sessions.clear_pending(request)
            return redirect(reverse("accounts:login") + "?again=1")
        return render(request, template, {"error": WRONG_CODE[method]})

    sessions.clear_pending(request)
    # login() rotates the session key again and stores the session auth hash.
    login(request, fresh, backend="django.contrib.auth.backends.ModelBackend")
    sessions.mark_signed_in(request)
    details: dict[str, Any] = {"method": method}
    if method == "recovery":
        details["recovery_codes_left"] = recovery.remaining(fresh)
    _audit(request, "admin.login", fresh.email, actor=fresh.email, **details)
    return HttpResponseRedirect(pending["next"])


@never_cache
@require_http_methods(["GET", "POST"])
def login_code_view(request: HttpRequest) -> HttpResponse:
    return _second_step(
        request,
        template="accounts/login_code.html",
        per_ip=limits.CODE_PER_IP,
        per_admin=limits.CODE_PER_ADMIN,
        method="totp",
    )


@never_cache
@require_http_methods(["GET", "POST"])
def login_recovery_view(request: HttpRequest) -> HttpResponse:
    return _second_step(
        request,
        template="accounts/login_recovery.html",
        per_ip=limits.RECOVERY_PER_IP,
        per_admin=limits.RECOVERY_PER_ADMIN,
        method="recovery",
    )


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        email = request.user.get_username()
        _audit(request, "admin.logout", email, actor=email)
    logout(request)
    return redirect(settings.LOGIN_URL)


@never_cache
@require_http_methods(["GET", "POST"])
def forgot_view(request: HttpRequest) -> HttpResponse:
    """Ask for a link to choose a new password. The reply never says who is an admin."""
    if not mail.enabled():
        return render(
            request,
            "accounts/forgot.html",
            {"no_email": True},
            status=(404 if request.method == "POST" else 200),
        )
    if request.method == "GET":
        return render(request, "accounts/forgot.html", {"email": ""})

    email = Admin.objects.normalize_email(request.POST.get("email", "")).strip()
    if not ratelimit.allow(limits.FORGOT_PER_IP, _identity(request)) or not ratelimit.allow(
        limits.FORGOT_PER_ACCOUNT, email
    ):
        context = {"email": email, "error": TOO_MANY}
        return render(request, "accounts/forgot.html", context, status=429)

    admin = passwords.resettable(email)
    if admin is not None:
        # The link is created and sent without waiting, so the reply does the
        # same work, and takes as long, as one for an address that is not an
        # admin. A failure is logged by mail.send, not shown.
        mail.in_background(lambda: passwords.send_link(admin, passwords.issue(admin)))
    _audit(request, "admin.password_reset_requested", email, admin=admin is not None)
    return render(request, "accounts/forgot_sent.html", {"email": email})
