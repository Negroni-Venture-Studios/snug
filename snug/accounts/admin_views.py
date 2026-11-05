"""Settings: the admins, inviting them, and the setup page invitees use."""

from typing import Any

import segno
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator, validate_email
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from snug.accounts import limits, setup, totp
from snug.accounts.models import Admin, AdminSetup
from snug.analytics import retention
from snug.audit.models import AuditEvent, record
from snug.core import mail, ratelimit
from snug.core.client import client_ip, user_agent
from snug.core.models import SiteSettings
from snug.nda import terms as nda_terms
from snug.nda.models import NdaVersion

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
        "done": next((k for k in ("invited", "removed", "reset") if request.GET.get(k) == "1"), ""),
        # Which section was just saved, so it can say so in place.
        "saved": request.GET.get("saved", ""),
        "privacy_url": SiteSettings.load().privacy_url,
        "retention_months": SiteSettings.load().reading_retention_months,
        "nda": nda_terms.current(),
        "nda_history": NdaVersion.objects.select_related("created_by")[:10],
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
        what=(f"{by.email} invited {email} to be an admin, at {_now()}."),
    )
    return redirect("/admin/settings/?invited=1")


# ---------------------------------------------------------------- privacy


def _checked_privacy_url(raw: str) -> str | None:
    """A plain https URL, or "" to remove the link. None if it will not do."""
    url = raw.strip()
    if not url:
        return ""
    if len(url) > 500 or any(c.isspace() for c in url):
        return None
    try:
        URLValidator(schemes=["https"])(url)
    except ValidationError:
        return None
    # No user name or password in the link: viewers would see them.
    return None if "@" in url.split("/", 3)[2] else url


@never_cache
@login_required
@require_POST
def privacy_view(request: HttpRequest) -> HttpResponse:
    url = _checked_privacy_url(request.POST.get("privacy_url", ""))
    if url is None:
        error = "Enter a full address starting https://, like https://example.com/privacy."
        return _settings_page(
            request,
            status=400,
            privacy_error=error,
            privacy_url=request.POST.get("privacy_url", "")[:500],
        )
    row = SiteSettings.load()
    if row.privacy_url != url:
        row.privacy_url = url
        row.save(update_fields=["privacy_url"])
        _audit(request, "settings.privacy_link_changed", _admin(request).email, "", url=url)
    return redirect("/admin/settings/?saved=privacy#privacy")


@never_cache
@login_required
@require_POST
def retention_view(request: HttpRequest) -> HttpResponse:
    """How many months reading data is kept after its link ends."""
    raw = request.POST.get("months", "").strip()
    if not raw.isdecimal() or not 1 <= int(raw) <= retention.MAX_MONTHS:
        error = f"Enter a whole number of months, from 1 to {retention.MAX_MONTHS}."
        return _settings_page(request, status=400, retention_error=error, retention_months=raw[:10])
    row = SiteSettings.load()
    months = int(raw)
    if row.reading_retention_months != months:
        row.reading_retention_months = months
        row.save(update_fields=["reading_retention_months"])
        _audit(request, "settings.retention_changed", _admin(request).email, "", months=months)
    return redirect("/admin/settings/?saved=retention#retention")


# ---------------------------------------------------------------- the NDA


@never_cache
@login_required
@require_POST
def nda_view(request: HttpRequest) -> HttpResponse:
    """Publish the NDA. Changed text becomes a new version, which everyone accepts again."""
    text = request.POST.get("text", "").strip()
    if not text or len(text) > nda_terms.MAX_TEXT:
        error = "Enter the agreement, of up to 50,000 characters."
        return _settings_page(request, status=400, nda_error=error, nda_draft=text[:60000])
    before = nda_terms.current()
    version = nda_terms.publish(text, by=_admin(request))
    if version != before:
        _audit(
            request,
            "settings.nda_published",
            _admin(request).email,
            "",
            version=version.number,
            sha256=version.sha256,
        )
    return redirect("/admin/settings/?saved=nda#nda")


# ---------------------------------------------------------------- removing and resetting


def _now() -> str:
    return f"{timezone.localtime():%H:%M on %d %B %Y}"


def _change_page(
    request: HttpRequest, target: Admin, action: str, *, error: str = "", status: int = 200
) -> HttpResponse:
    context = {"tab": "settings", "target": target, "action": action, "error": error}
    return render(request, "accounts/confirm.html", context, status=status)


def _target(request: HttpRequest) -> Admin:
    source = request.POST if request.method == "POST" else request.GET
    email = Admin.objects.normalize_email(source.get("email", ""))[:254]
    return get_object_or_404(Admin, email=email)


def _refused(request: HttpRequest, target: Admin, action: str) -> HttpResponse | None:
    refused = _confirmed(request, action)
    if refused is None:
        return None
    status = 429 if refused == TOO_MANY else 400
    return _change_page(request, target, action, error=refused, status=status)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def remove_view(request: HttpRequest) -> HttpResponse:
    by = _admin(request)
    target = _target(request)
    if request.method == "GET":
        return _change_page(request, target, "remove")
    if target == by:
        error = "You cannot remove yourself. Ask another admin to remove you."
        return _change_page(request, target, "remove", error=error, status=400)
    refused = _refused(request, target, "remove")
    if refused:
        return refused

    email = target.email
    if not setup.remove(target):
        error = "That would leave no admins. The last admin cannot be removed."
        return _change_page(request, target, "remove", error=error, status=400)
    _audit(request, "admin.removed", by.email, email)
    setup.alert_admins(
        subject=f"{email} was removed as an admin",
        what=f"{by.email} removed {email} as an admin, at {_now()}.",
        also=email,
    )
    return redirect("/admin/settings/?removed=1")


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def reset_view(request: HttpRequest) -> HttpResponse:
    by = _admin(request)
    target = _target(request)
    if request.method == "GET":
        return _change_page(request, target, "reset")
    if target == by:
        error = (
            "To replace your own authenticator, sign in with a recovery code, "
            "or ask another admin to reset it."
        )
        return _change_page(request, target, "reset", error=error, status=400)
    if target.totp_confirmed_at is None:
        error = f"{target.email} has not finished setting up. Invite them again instead."
        return _change_page(request, target, "reset", error=error, status=400)
    if not mail.enabled():
        error = (
            "Resetting needs email, to send them a setup link. On the server, use snug reset-2fa."
        )
        return _change_page(request, target, "reset", error=error, status=503)
    refused = _refused(request, target, "reset")
    if refused:
        return refused

    # Send first: if the email fails, nothing changes, rather than leaving
    # them with no authenticator and no way to set up a new one.
    token = setup.new_token()
    if not setup.send_reset(target, token, by):
        error = "The email could not be sent, so nothing has changed. Try again."
        return _change_page(request, target, "reset", error=error, status=503)
    setup.reset_second_factor(target, token, by)
    _audit(request, "admin.second_factor_reset", by.email, target.email)
    setup.alert_admins(
        subject=f"The second factor for {target.email} was reset",
        what=(
            f"{by.email} reset the second factor for {target.email}, at {_now()}. "
            f"They have been emailed a link to set up their authenticator again."
        ),
    )
    return redirect("/admin/settings/?reset=1")


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
