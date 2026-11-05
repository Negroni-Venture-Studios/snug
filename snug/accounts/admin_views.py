"""Settings: the admins, inviting them, and the setup page invitees use."""

from typing import Any

import segno
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from snug.accounts import limits, setup, totp
from snug.accounts.models import Admin, AdminSetup
from snug.audit.models import AuditEvent, record
from snug.core import mail, ratelimit
from snug.core.client import client_ip, user_agent

SETUP_SESSION = "snug.setup"
WRONG_CODE = "That code is not right, or it has just been used. Wait for the next one."
TOO_MANY = "Too many attempts. Wait 15 minutes, then try again."


def _admin(request: HttpRequest) -> Admin:
    user = request.user
    assert isinstance(user, Admin)  # noqa: S101 - every settings view requires a signed-in admin
    return user


def _audit(request: HttpRequest, action: str, actor: str, target: str, **details: Any) -> None:
    record(
        action,
        actor_type=AuditEvent.ActorType.ADMIN,
        actor=actor,
        target=target[:254],
        ip_address=client_ip(request),
        user_agent=user_agent(request),
        details=details,
    )


def _settings_page(request: HttpRequest, *, status: int = 200, **extra: Any) -> HttpResponse:
    now = timezone.now()
    pending = {
        row.admin_id: row
        for row in AdminSetup.objects.filter(expires_at__gt=now).select_related("admin")
    }
    admins = [{"account": a, "setup": pending.get(a.pk)} for a in Admin.objects.all()]
    context = {
        "tab": "settings",
        "admins": admins,
        "email_enabled": mail.enabled(),
        "invited": request.GET.get("invited") == "1",
        "values": {"email": ""},
        **extra,
    }
    return render(request, "accounts/settings.html", context, status=status)


def _confirmed(request: HttpRequest, action: str) -> str | None:
    """Check the acting admin's fresh code. Returns an error, or None if confirmed."""
    admin = _admin(request)
    if not ratelimit.allow(limits.CONFIRM_PER_ADMIN, str(admin.pk)):
        _audit(request, "admin.confirm_failed", admin.email, "", change=action, reason="limited")
        return TOO_MANY
    if not setup.confirm(admin, request.POST.get("code", "")):
        _audit(request, "admin.confirm_failed", admin.email, "", change=action, reason="wrong")
        return WRONG_CODE
    return None


@never_cache
@login_required
@require_safe
def settings_view(request: HttpRequest) -> HttpResponse:
    return _settings_page(request)


@never_cache
@login_required
@require_POST
def invite_view(request: HttpRequest) -> HttpResponse:
    by = _admin(request)
    email = Admin.objects.normalize_email(request.POST.get("email", ""))[:254]
    values = {"email": email}
    if not mail.enabled():
        return _settings_page(request, status=503, values=values)
    try:
        validate_email(email)
    except ValidationError:
        error = "Enter the email address of the person to invite."
        return _settings_page(request, status=400, invite_error=error, values=values)

    refused = _confirmed(request, "invite")
    if refused:
        status = 429 if refused == TOO_MANY else 400
        return _settings_page(request, status=status, code_error=refused, values=values)

    account = setup.invitable(email)
    if account is None:
        error = "That address is already an admin."
        return _settings_page(request, status=400, invite_error=error, values=values)
    account.save()
    token = setup.issue(account, AdminSetup.Purpose.INVITE, by)
    if not setup.send_invite(account, token, by):
        AdminSetup.objects.filter(admin=account).delete()
        error = "The invite could not be sent. Check the email settings, then try again."
        return _settings_page(request, status=503, invite_error=error, values=values)

    _audit(request, "admin.invited", by.email, email)
    setup.alert_admins(
        subject=f"{email} was invited as an admin",
        what=(
            f"{by.email} invited {email} to be an admin, "
            f"at {timezone.localtime():%H:%M on %d %B %Y}."
        ),
    )
    return redirect("/admin/settings/?invited=1")


# ---------------------------------------------------------------- setup


def _qr(secret: str, email: str) -> str:
    uri = totp.provisioning_uri(secret, email, settings.CONFIG.domain)
    return str(
        segno.make(uri, error="m").svg_inline(
            scale=5,
            border=2,
            dark="#000",
            light="#fff",
            svgclass="qr",
            lineclass=None,
            omitsize=True,
        )
    )


def _secret_for(request: HttpRequest, row: AdminSetup) -> str | None:
    held = request.session.get(SETUP_SESSION)
    if held and held.get("setup") == row.pk:
        return str(held["secret"])
    return None


def _setup_page(
    request: HttpRequest,
    row: AdminSetup,
    secret: str,
    *,
    errors: dict[str, str] | None = None,
    status: int = 200,
) -> HttpResponse:
    context = {
        "row": row,
        "secret": secret,
        "key": totp.grouped(secret),
        "qr": _qr(secret, row.admin.email),
        "invite": row.purpose == AdminSetup.Purpose.INVITE,
        "errors": errors or {},
    }
    return render(request, "accounts/setup.html", context, status=status)


@never_cache
@require_http_methods(["GET", "POST"])
def setup_view(request: HttpRequest, token: str) -> HttpResponse:
    row = setup.find(token)
    if row is None:
        return render(request, "accounts/setup_gone.html", status=404)

    secret = _secret_for(request, row)
    if request.method == "GET" or secret is None:
        if secret is None:
            secret = totp.new_secret()
            request.session[SETUP_SESSION] = {"setup": row.pk, "secret": secret}
        if request.method == "POST":
            expired = {"code": "This page had expired. Scan the QR code below, then try again."}
            return _setup_page(request, row, secret, errors=expired, status=400)
        return _setup_page(request, row, secret)

    if not ratelimit.allow(limits.SETUP_PER_IP, client_ip(request) or "unknown"):
        return _setup_page(request, row, secret, errors={"code": TOO_MANY}, status=429)

    errors: dict[str, str] = {}
    password: str | None = None
    if row.purpose == AdminSetup.Purpose.INVITE:
        password = request.POST.get("password", "")
        if password != request.POST.get("password2", ""):
            errors["password"] = "The passwords do not match."  # noqa: S105 - a message
        else:
            try:
                validate_password(password, user=row.admin)
            except ValidationError as problem:
                errors["password"] = " ".join(problem.messages)
    step = totp.verify(secret, request.POST.get("code", ""), last_step=0)
    if step is None:
        errors["code"] = "That code is not right. Check the app's clock is set automatically."
    if errors or step is None:
        return _setup_page(request, row, secret, errors=errors, status=400)

    email = row.admin.email
    purpose = row.purpose
    codes = setup.complete(row, password=password, secret=secret, step=step)
    request.session.pop(SETUP_SESSION, None)
    if codes is None:
        return render(request, "accounts/setup_gone.html", status=404)
    _audit(request, "admin.setup_completed", email, email, purpose=purpose)
    return render(request, "accounts/setup_done.html", {"email": email, "codes": codes})
