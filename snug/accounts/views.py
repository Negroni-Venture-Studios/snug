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

from snug.accounts import limits, recovery, sessions, totp
from snug.accounts.models import Admin
from snug.audit.models import AuditEvent, record
from snug.core import ratelimit
from snug.core.client import client_ip, user_agent

TOO_MANY = "Too many attempts. Wait 15 minutes, then try again."


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
    context: dict[str, Any] = {"next": request.GET.get("next", ""), "email": ""}
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

    sessions.start_pending(request, admin.pk, _safe_next(request, context["next"]))
    return redirect("accounts:login_code")


def _second_step(
    request: HttpRequest,
    *,
    template: str,
    per_ip: ratelimit.Limit,
    per_admin: ratelimit.Limit,
    method: str,
) -> HttpResponse:
    pending = sessions.get_pending(request)
    if pending is None:
        return redirect("accounts:login")
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
    if method == "totp":
        with transaction.atomic():
            # Re-read inside the write transaction, so two requests carrying
            # the same code cannot both pass the replay check.
            fresh = Admin.objects.get(pk=admin.pk)
            step = totp.verify(fresh.totp_secret, submitted, last_step=fresh.totp_last_step)
            if step is not None:
                fresh.totp_last_step = step
                fresh.save(update_fields=["totp_last_step"])
        ok = step is not None
    else:
        ok = recovery.use(admin, submitted)

    if not ok:
        _audit(request, "admin.login_failed", admin.email, step=method, reason="wrong")
        if sessions.record_code_failure(request, pending) >= limits.CODE_FAILURES_PER_PASSWORD:
            sessions.clear_pending(request)
            return redirect(reverse("accounts:login") + "?again=1")
        return render(request, template, {"error": "That code is not right."})

    sessions.clear_pending(request)
    # login() rotates the session key again and stores the session auth hash.
    login(request, admin, backend="django.contrib.auth.backends.ModelBackend")
    sessions.mark_signed_in(request)
    details: dict[str, Any] = {"method": method}
    if method == "recovery":
        details["recovery_codes_left"] = recovery.remaining(admin)
    _audit(request, "admin.login", admin.email, actor=admin.email, **details)
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
