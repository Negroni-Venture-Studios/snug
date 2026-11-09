"""Settings: the admins, inviting them, and the setup page invitees use."""

import contextlib
import hmac
import secrets
import time
from typing import Any

import segno
from django.conf import settings
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator, validate_email
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from snug.accounts import limits, passwords, recovery, setup, totp
from snug.accounts.models import Admin, AdminSetup
from snug.analytics import retention
from snug.audit.models import AuditEvent, record
from snug.core import brand, gmail, logo, mail, ratelimit
from snug.core.client import client_ip, user_agent
from snug.core.models import EmailSettings, SiteSettings, company_name
from snug.nda import terms as nda_terms
from snug.nda.models import NdaVersion
from snug.rendering import sandbox

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


# The guides each Settings section links to. Snug has no tagged releases yet, so
# they are the latest; once it has, link to the running version's tag instead.
DOCS_URL = "https://github.com/Negroni-Venture-Studios/snug/blob/main/docs/"


def _settings_page(request: HttpRequest, *, status: int = 200, **extra: Any) -> HttpResponse:
    now = timezone.now()
    pending = {
        row.admin_id: row
        for row in AdminSetup.objects.filter(expires_at__gt=now)
        .exclude(purpose=AdminSetup.Purpose.PASSWORD)  # private to the admin who asked
        .select_related("admin")
    }
    admins = [{"account": a, "setup": pending.get(a.pk)} for a in Admin.objects.all()]
    context = {
        "tab": "settings",
        "docs_url": DOCS_URL,
        "admins": admins,
        "email_enabled": mail.enabled(),
        "done": next((k for k in ("invited", "removed", "reset") if request.GET.get(k) == "1"), ""),
        # Which section was just saved, so it can say so in place.
        "saved": request.GET.get("saved", ""),
        "privacy_url": SiteSettings.load().privacy_url,
        **_branding_context(request),
        "retention_months": SiteSettings.load().reading_retention_months,
        "nda": nda_terms.current(),
        "nda_history": NdaVersion.objects.select_related("created_by")[:10],
        "values": {"email": ""},
        "email": _email_context(request, extra.pop("email_form", None)),
        # Google's refusal, held over its redirect back to step 2, shown once.
        "google_error": request.session.pop(GOOGLE_ERROR, ""),
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
    return redirect("/admin/settings/?invited=1#invite")


# ---------------------------------------------------------------- your own password


@never_cache
@login_required
@require_POST
def change_password_view(request: HttpRequest) -> HttpResponse:
    """Change your own password: the current one, the new one twice, and a fresh code.

    This browser stays signed in; every other session ends, because each
    session holds a hash of the password.
    """
    admin = _admin(request)

    def refused(status: int, field: str, error: str) -> HttpResponse:
        return _settings_page(request, status=status, account_errors={field: error})

    # One allowance for the whole attempt, so the current password cannot be
    # guessed here any faster than a code can.
    if not ratelimit.allow(limits.CONFIRM_PER_ADMIN, str(admin.pk)):
        _audit(request, "admin.password_change_failed", admin.email, admin.email, reason="limited")
        return refused(429, "code", TOO_MANY)
    # The new password first: a weak one must not spend a code.
    password = request.POST.get("password", "")
    if password != request.POST.get("password2", ""):
        return refused(400, "password", "The passwords do not match.")
    try:
        validate_password(password, user=admin)
    except ValidationError as problem:
        return refused(400, "password", " ".join(problem.messages))
    if not admin.check_password(request.POST.get("current", "")):
        _audit(request, "admin.password_change_failed", admin.email, admin.email, reason="password")
        return refused(400, "current", "That is not your current password.")
    if not setup.confirm(admin, request.POST.get("code", "")):
        _audit(request, "admin.password_change_failed", admin.email, admin.email, reason="code")
        return refused(400, "code", WRONG_CODE)

    admin = Admin.objects.get(pk=admin.pk)  # with the step confirm() just recorded
    admin.set_password(password)
    admin.save(update_fields=["password"])
    update_session_auth_hash(request, admin)  # this browser stays signed in
    _audit(request, "admin.password_changed", admin.email, admin.email)
    passwords.send_changed(admin, how="from Settings, while signed in")
    return redirect("/admin/settings/?saved=password#account")


@never_cache
@login_required
@require_POST
def replace_authenticator_view(request: HttpRequest) -> HttpResponse:
    """Replace your own authenticator: the password, and a code from the old app or a recovery code.

    The old authenticator and recovery codes stop working at once, which also
    ends every session, this one included. The admin goes straight to the setup
    page for the new one; the link is also emailed, in case they close the tab.
    """
    admin = _admin(request)

    def refused(status: int, field: str, error: str) -> HttpResponse:
        return _settings_page(request, status=status, authenticator_errors={field: error})

    if not ratelimit.allow(limits.CONFIRM_PER_ADMIN, str(admin.pk)):
        _audit(request, "admin.replace_failed", admin.email, admin.email, reason="limited")
        return refused(429, "code", TOO_MANY)
    if not admin.check_password(request.POST.get("current", "")):
        _audit(request, "admin.replace_failed", admin.email, admin.email, reason="password")
        return refused(400, "current", "That is not your current password.")
    code = request.POST.get("code", "")
    if passwords.is_app_code(code):
        method, ok = "totp", setup.confirm(admin, code)
    else:
        method, ok = "recovery", recovery.use(admin, code)
    if not ok:
        _audit(request, "admin.replace_failed", admin.email, admin.email, reason="code")
        return refused(400, "code", WRONG_CODE)

    token = setup.new_token()
    setup.reset_second_factor(admin, token, admin)
    if mail.enabled():
        setup.send_replace(admin, token)
    _audit(request, "admin.second_factor_replaced", admin.email, admin.email, method=method)
    setup.alert_admins(
        subject=f"{admin.email} replaced their authenticator",
        what=f"{admin.email} replaced their authenticator, at {_now()}.",
    )
    return redirect(setup.setup_url(token).removeprefix(settings.CONFIG.origin))


# ---------------------------------------------------------------- branding

COMPANY_MAX = 100
DEFAULT_ACCENT = brand.DEFAULT


def _branding_context(request: HttpRequest) -> dict[str, Any]:
    row = SiteSettings.load()
    accent = row.accent_colour
    return {
        "accent": accent,
        "accent_shown": accent or DEFAULT_ACCENT,
        "brand_css": f"/brand/brand.css?v={brand.version(accent)}" if accent else "",
        "brand_adjusted": bool(accent) and brand.palette(accent).adjusted,
        "company": company_name(),
        "company_value": row.company_name or settings.CONFIG.company_name,
        "show_name": row.show_name_with_logo,
        "logo_url": f"/brand/{row.logo_name}" if row.logo_name else "",
        "icon_url": f"/brand/{row.icon_name}" if row.icon_name else "",
        "favicon": logo.favicon(row.logo_name, row.icon_name, company_name(), accent)[0],
    }


@never_cache
@login_required
@require_POST
def branding_view(request: HttpRequest) -> HttpResponse:
    """The company's name and the accent colour on investors' pages.

    An empty name falls back to SNUG_COMPANY_NAME; an empty colour, to Snug's
    default. A form that leaves out the name leaves it as it is.
    """
    row = SiteSettings.load()
    raw = request.POST.get("accent", "").strip()
    accent = brand.parse(raw) if raw else ""
    typed = request.POST.get("company")
    company = row.company_name if typed is None else " ".join(typed.split())
    # The checkbox is only sent with the name; a form without it leaves both.
    show_name = row.show_name_with_logo if typed is None else bool(request.POST.get("show_name"))
    errors: dict[str, Any] = {}
    if accent is None:
        errors.update(branding_error="Enter a colour like #1f5f8b.", accent_typed=raw[:20])
    if len(company) > COMPANY_MAX:
        errors.update(company_error=f"At most {COMPANY_MAX} characters.")
    if errors:
        return _settings_page(request, status=400, company_value=company[:200], **errors)
    changed = {
        field: value
        for field, value, old in (
            ("company", company, row.company_name),
            ("accent", accent, row.accent_colour),
            ("show_name", show_name, row.show_name_with_logo),
        )
        if value != old
    }
    if changed:
        row.company_name, row.accent_colour = company, str(accent)
        row.show_name_with_logo = show_name
        row.save(update_fields=["company_name", "accent_colour", "show_name_with_logo"])
        _audit(request, "settings.branding_changed", _admin(request).email, "", **changed)
    return redirect("/admin/settings/?saved=branding#branding")


MEGABYTES = logo.MAX_BYTES // (1024 * 1024)
NOT_AN_IMAGE = "Upload a PNG or JPEG image."


def _picture(request: HttpRequest, kind: logo.Kind) -> HttpResponse:
    """Upload, replace or remove the founder's logo or tab icon."""
    row = SiteSettings.load()
    field = f"{kind}_name"
    old: str = getattr(row, field)
    if request.POST.get("remove"):
        name = ""
    else:
        upload = request.FILES.get(kind)
        error = None
        if upload is None:
            error = "Choose a PNG or JPEG image."
        elif upload.size is None or upload.size > logo.MAX_BYTES:
            error = f"The {kind} must be at most {MEGABYTES} MB."
        else:
            try:
                name = logo.store(logo.draw(upload, kind), kind)
            except sandbox.WorkerError as refused:
                # Only the server's own problem gets its own message; anything
                # about the file is the same advice.
                error = str(refused) if refused.code == "unconfined" else NOT_AN_IMAGE
        if error:
            return _settings_page(request, status=400, **{f"{kind}_error": error})
    if name != old:
        setattr(row, field, name)
        row.save(update_fields=[field])
        logo.discard(old)
        _audit(request, f"settings.{kind}_changed", _admin(request).email, "", **{kind: name})
    return redirect(f"/admin/settings/?saved={kind}#branding-{kind}")


@never_cache
@login_required
@require_POST
def logo_view(request: HttpRequest) -> HttpResponse:
    return _picture(request, "logo")


@never_cache
@login_required
@require_POST
def icon_view(request: HttpRequest) -> HttpResponse:
    return _picture(request, "icon")


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


# ---------------------------------------------------------------- email


def _describe(now: mail.Outgoing) -> str:
    if now.kind == "google":
        return f"It now sends as {now.from_address}, through Google Workspace."
    if now.kind == "smtp":
        return f"It now sends through {now.host} as {now.from_address}."
    if now.kind == "outbox":
        return "It now writes emails to the outbox folder."
    return "Email is now off."


def _same_delivery(before: mail.Outgoing, after: mail.Outgoing) -> bool:
    """Whether email still goes the same way, through the same account.

    Another password or token at the same server would reroute mail just as
    surely as another server, so the secrets count too. They are compared in
    memory, in constant time, and never stored or logged.
    """
    plain = ("kind", "source", "host", "port", "username", "security", "from_address", "client_id")
    secret = ("password", "client_secret", "refresh_token")
    return all(getattr(before, name) == getattr(after, name) for name in plain) and all(
        hmac.compare_digest(getattr(before, name).encode(), getattr(after, name).encode())
        for name in secret
    )


def _alert_if_changed(request: HttpRequest, before: mail.Outgoing) -> None:
    """Tell every admin when how Snug sends email has changed.

    Through the settings in force before, so whoever made the change cannot
    swallow the alert (unless email was off, when there is no other way).
    """
    after = mail.outgoing()
    if _same_delivery(before, after):
        return
    setup.alert_admins(
        subject="Email settings changed",
        what=f"{_admin(request).email} changed how Snug sends email. {_describe(after)}",
        via=before if before.kind != "off" else None,
    )


def _let_go_of_google(row: EmailSettings) -> str:
    """Forget Snug's permission at Google. Returns the token, for _revoke after
    the alert: the alert may need to go through that very permission."""
    token = row.google_refresh_token
    row.google_refresh_token = ""
    row.google_account = ""
    row.google_connected_at = None
    return token


def _revoke(token: str) -> None:
    """Withdraw a forgotten permission at Google. Best effort: it is gone from Snug."""
    if token:
        with contextlib.suppress(gmail.GoogleError):
            gmail.revoke(token)
    gmail.forget_tokens()


def _email_status(now: mail.Outgoing) -> str:
    if now.kind == "google":
        return f"Snug sends email as {now.from_address}, through Google Workspace."
    if now.source == "settings":
        return f"Snug sends email through {now.host} as {now.from_address}."
    if now.source == "snug.env":
        return (
            f"Using the SMTP settings in snug.env: {now.host}, as {now.from_address}. "
            "Saving here replaces them."
        )
    if now.kind == "outbox":
        return "On this computer, emails are written to the outbox folder in the data folder."
    return "Email is off, so investors cannot be sent codes and admins cannot be invited."


def _email_fallback() -> str:
    """What sends email once the settings saved here are cleared."""
    config = settings.CONFIG
    if config.smtp_host:
        return f"Email then uses the SMTP settings in snug.env, through {config.smtp_host}."
    if config.email_outbox:
        return "Emails are then written to the outbox folder, as on any computer without email."
    return "Email is then off, so investors cannot be sent codes, until it is set up again."


def _email_context(request: HttpRequest, form: dict[str, Any] | None) -> dict[str, Any]:
    """The Email panel: what is in force, and the saved values (or those just typed)."""
    row = EmailSettings.load()
    now = mail.outgoing()
    pending = request.session.get(GOOGLE_PENDING)
    values = {
        "provider": row.provider or "smtp",
        "google_client_id": row.google_client_id,
        "google_from": row.google_from_address,
        "from_address": row.from_address,
        "smtp_host": row.smtp_host,
        "smtp_port": row.smtp_port or "",
        "smtp_username": row.smtp_username,
        "smtp_security": row.smtp_security or "starttls",
    }
    errors: dict[str, str] = {}
    if form is not None:
        values, errors = form["values"], form["errors"]
    return {
        "status": _email_status(now),
        "host": now.host,
        "from_address": now.from_address,
        "values": values,
        "errors": errors,
        "password_saved": bool(row.smtp_password),
        "google_secret_saved": bool(row.google_client_secret),
        "google_ready": bool(row.google_client_id and row.google_client_secret),
        "google_account": row.google_account if row.google_refresh_token else "",
        "google_connected_at": row.google_connected_at,
        "google_pending": pending["account"] if isinstance(pending, dict) else "",
        "kind": now.kind,
        "ports": mail.PORTS_IN_WORDS,
        # Anything saved here, working or not, which Clear email settings would forget.
        "saved_here": any(
            (
                row.provider,
                row.from_address,
                row.smtp_host,
                row.smtp_port,
                row.smtp_username,
                row.smtp_password,
                row.google_client_id,
                row.google_client_secret,
                row.google_from_address,
                row.google_refresh_token,
            )
        ),
        "fallback": _email_fallback(),
        "source": now.source,
        "callback_url": gmail.callback_url(),
    }


@never_cache
@login_required
@require_POST
def email_view(request: HttpRequest) -> HttpResponse:
    """Save how Snug sends email. Passwords and secrets are kept unless new ones are typed."""
    if request.POST.get("provider") == "google":
        return _save_google_client(request)
    post = request.POST
    values = {
        "provider": "smtp",
        "from_address": post.get("from_address", "").strip()[:254],
        "smtp_host": post.get("smtp_host", "").strip()[:253],
        "smtp_port": post.get("smtp_port", "").strip()[:10],
        "smtp_username": post.get("smtp_username", "").strip()[:254],
        "smtp_security": post.get("smtp_security", "starttls"),
    }
    errors: dict[str, str] = {}
    try:
        validate_email(values["from_address"])
    except ValidationError:
        errors["from_address"] = "Enter the address emails come from, like rooms@example.com."
    port = values["smtp_port"]
    if not (port.isdecimal() and int(port) in mail.SMTP_PORTS):
        errors["smtp_port"] = f"Use port {mail.PORTS_IN_WORDS}."
    if not values["smtp_host"]:
        errors["smtp_host"] = "Enter your provider's SMTP server, like smtp.postmarkapp.com."
    if values["smtp_security"] not in ("starttls", "tls"):
        errors["smtp_security"] = (
            "Choose STARTTLS or TLS. Email must never cross the internet unencrypted."
        )
    if not errors:
        # Email carries codes and reset links: changing where it goes is
        # confirmed like any change to the admins. Checked before the server's
        # name is looked up, so nobody without a code learns which names exist.
        refused = _confirmed(request, "email")
        if refused:
            errors["code"] = refused
        else:
            where = mail.public_server(values["smtp_host"], int(port))
            if where == "private":
                errors["smtp_host"] = (
                    "That server is on a private network. "
                    "Snug sends only through public mail servers."
                )
            elif where:
                errors["smtp_host"] = "That server cannot be found. Check its name."
    if errors:
        form = {"values": values, "errors": errors}
        status = 429 if errors.get("code") == TOO_MANY else 400
        return _settings_page(request, status=status, email_form=form)

    before = mail.outgoing()
    row = EmailSettings.load()
    row.provider = "smtp"
    row.from_address = values["from_address"]
    row.smtp_host = values["smtp_host"]
    row.smtp_port = int(port)
    row.smtp_username = values["smtp_username"]
    row.smtp_security = values["smtp_security"]
    password = post.get("smtp_password", "")
    if password:
        row.smtp_password = password[:500]
    dropped = _let_go_of_google(row)
    row.save()
    _audit(
        request,
        "settings.email_changed",
        _admin(request).email,
        "",
        provider="smtp",
        host=row.smtp_host,
        **{"from": row.from_address},
    )
    _alert_if_changed(request, before)
    _revoke(dropped)
    return redirect("/admin/settings/?saved=email#email-test")


# ---------------------------------------------------------------- Google Workspace

GOOGLE_STATE = "snug.google.state"
GOOGLE_PENDING = "snug.google.pending"
GOOGLE_ERROR = "snug.google.error"
# How long the round trip to Google, and then entering a code, may take.
GOOGLE_MINUTES = 10


# The Email section's steps. Each save lands on the step to do next.
MAILBOX_STEP = "google-mailbox"
TEST_STEP = "email-test"


def _google_redirect(saved: str, step: str = MAILBOX_STEP) -> HttpResponse:
    return redirect(f"/admin/settings/?saved={saved}#{step}")


def _save_google_client(request: HttpRequest) -> HttpResponse:
    """Save the founder's OAuth client: the ID, and the secret unless it is kept."""
    row = EmailSettings.load()
    client_id = request.POST.get("google_client_id", "").strip()[:200]
    secret = request.POST.get("google_client_secret", "").strip()[:200]
    google_from = request.POST.get("google_from", "").strip()[:254]
    values = {**_email_context(request, None)["values"], "provider": "google"}
    values["google_client_id"] = client_id
    values["google_from"] = google_from
    errors: dict[str, str] = {}
    if google_from:
        try:
            validate_email(google_from)
        except ValidationError:
            errors["google_from"] = "Enter an address, like rooms@example.com, or leave it empty."
    shape = client_id.removesuffix(gmail.CLIENT_SUFFIX)
    if not (client_id.endswith(gmail.CLIENT_SUFFIX) and shape and shape.replace("-", "").isalnum()):
        errors["google_client_id"] = "Paste the client ID, which ends .apps.googleusercontent.com."
    if not secret and not row.google_client_secret:
        errors["google_client_secret"] = "Paste the client secret."  # noqa: S105 - a message
    if not errors:
        refused = _confirmed(request, "email")
        if refused:
            errors["code"] = refused
    if errors:
        status = 429 if errors.get("code") == TOO_MANY else 400
        form = {"values": values, "errors": errors}
        return _settings_page(request, status=status, email_form=form)
    before = mail.outgoing()
    dropped = ""
    if row.google_client_id != client_id:
        # Another client: the mailbox must approve it afresh.
        dropped = _let_go_of_google(row)
    row.provider = "google"
    row.google_client_id = client_id
    row.google_from_address = google_from
    if secret:
        row.google_client_secret = secret
    row.save()
    _audit(
        request,
        "settings.email_changed",
        _admin(request).email,
        "",
        provider="google",
        client=client_id,
    )
    _alert_if_changed(request, before)
    _revoke(dropped)
    return _google_redirect("email")


@never_cache
@login_required
@require_safe
def google_connect_view(request: HttpRequest) -> HttpResponse:
    """Send the admin to Google to approve the sending mailbox.

    A link, not a form: the content security policy's form-action would stop
    a form's redirect to Google. Saving needs a fresh code afterwards.
    """
    row = EmailSettings.load()
    if not (row.google_client_id and row.google_client_secret):
        return _google_redirect("google-no-client")
    state = secrets.token_urlsafe(32)
    verifier, challenge = gmail.new_verifier()
    request.session[GOOGLE_STATE] = {"state": state, "verifier": verifier, "at": time.time()}
    request.session.pop(GOOGLE_PENDING, None)
    return HttpResponseRedirect(gmail.auth_url(row.google_client_id, state, challenge))


def _google_error(request: HttpRequest, message: str) -> HttpResponse:
    """Back to step 2 with the message, so Google's one-time code leaves the address bar."""
    request.session[GOOGLE_ERROR] = message
    return redirect(f"/admin/settings/#{MAILBOX_STEP}")


@never_cache
@login_required
@require_safe
def google_callback_view(request: HttpRequest) -> HttpResponse:
    """Google sends the admin back here. Holds the approval until a code confirms it."""
    started = request.session.pop(GOOGLE_STATE, None)
    state = request.GET.get("state", "")
    if (
        not isinstance(started, dict)
        or time.time() - float(started.get("at", 0)) > GOOGLE_MINUTES * 60
        or not hmac.compare_digest(str(started.get("state", "")).encode(), state.encode())
    ):
        return _google_error(
            request, "That did not come from this Snug, or took too long. Try again."
        )
    denied = request.GET.get("error", "")
    if denied:
        name = denied if denied.replace("_", "").isalnum() else "refused"
        return _google_error(request, f"Google did not give Snug permission ({name[:40]}).")
    row = EmailSettings.load()
    try:
        refresh_token, account = gmail.exchange(
            request.GET.get("code", ""),
            str(started["verifier"]),
            row.google_client_id,
            row.google_client_secret,
        )
    except gmail.GoogleError as error:
        return _google_error(request, f"Google refused the connection ({error}).")
    if not refresh_token or not account:
        return _google_error(
            request,
            "Google did not give Snug a lasting permission. Remove Snug under Third-party "
            "apps at myaccount.google.com/connections, then connect again.",
        )
    request.session[GOOGLE_PENDING] = {
        "refresh_token": refresh_token,
        "account": account,
        "at": time.time(),
    }
    return _google_redirect("google-pending")


@never_cache
@login_required
@require_POST
def google_confirm_view(request: HttpRequest) -> HttpResponse:
    """A fresh code finishes connecting the mailbox, and every admin is told."""
    pending = request.session.get(GOOGLE_PENDING)
    if (
        not isinstance(pending, dict)
        or time.time() - float(pending.get("at", 0)) > GOOGLE_MINUTES * 60
    ):
        request.session.pop(GOOGLE_PENDING, None)
        return _google_redirect("google-expired")
    refused = _confirmed(request, "email")
    if refused:
        status = 429 if refused == TOO_MANY else 400
        form = {
            "values": _email_context(request, None)["values"],
            "errors": {"google_code": refused},
        }
        return _settings_page(request, status=status, email_form=form)
    request.session.pop(GOOGLE_PENDING, None)
    before = mail.outgoing()
    row = EmailSettings.load()
    row.provider = "google"
    # Reconnecting replaces the old permission: withdrawn once the alert is out.
    replaced = row.google_refresh_token
    row.google_refresh_token = str(pending["refresh_token"])
    row.google_account = str(pending["account"])
    row.google_connected_at = timezone.now()
    row.save()
    gmail.forget_tokens()
    admin = _admin(request)
    _audit(request, "settings.email_google_connected", admin.email, "", account=row.google_account)
    _alert_if_changed(request, before)
    if replaced != row.google_refresh_token:
        _revoke(replaced)
    return _google_redirect("email-google", TEST_STEP)


@never_cache
@login_required
@require_POST
def email_clear_view(request: HttpRequest) -> HttpResponse:
    """Forget every email setting saved here, and withdraw Snug's permission at Google.

    Email then falls back to snug.env, the outbox on localhost, or off. Turning
    it off stops investors' codes, so it needs a fresh code, and every admin is
    told through the route in force before, while it still works.
    """
    refused = _confirmed(request, "email")
    if refused:
        status = 429 if refused == TOO_MANY else 400
        values = _email_context(request, None)["values"]
        form = {"values": values, "errors": {"clear_code": refused}}
        return _settings_page(request, status=status, email_form=form)
    before = mail.outgoing()
    row = EmailSettings.load()
    dropped = _let_go_of_google(row)
    row.provider = ""
    row.from_address = row.smtp_host = row.smtp_username = row.smtp_password = ""
    row.smtp_security = ""
    row.smtp_port = None
    row.google_client_id = row.google_client_secret = row.google_from_address = ""
    row.save()
    request.session.pop(GOOGLE_PENDING, None)
    _audit(request, "settings.email_cleared", _admin(request).email, "")
    _alert_if_changed(request, before)
    _revoke(dropped)
    return redirect("/admin/settings/?saved=email-cleared#email")


@never_cache
@login_required
@require_POST
def google_disconnect_view(request: HttpRequest) -> HttpResponse:
    """Stop sending through Google, and withdraw Snug's permission there.

    Turning email off stops investors' codes and admins' alerts, so it needs a
    fresh code too, and every admin is told, through Google, while it still can.
    """
    refused = _confirmed(request, "email")
    if refused:
        status = 429 if refused == TOO_MANY else 400
        values = _email_context(request, None)["values"]
        form = {"values": values, "errors": {"google_disconnect_code": refused}}
        return _settings_page(request, status=status, email_form=form)
    before = mail.outgoing()
    row = EmailSettings.load()
    account = row.google_account
    dropped = _let_go_of_google(row)
    row.save()
    _audit(
        request,
        "settings.email_google_disconnected",
        _admin(request).email,
        "",
        account=account,
    )
    _alert_if_changed(request, before)
    _revoke(dropped)
    return _google_redirect("email-google-off")


@never_cache
@login_required
@require_POST
def email_test_view(request: HttpRequest) -> HttpResponse:
    """Send a short email to the admin who asked, with the settings in force."""
    admin = _admin(request)
    if not ratelimit.allow(limits.EMAIL_TEST_PER_ADMIN, str(admin.pk)):
        error = "Too many test emails. Try again in 15 minutes."
        return _settings_page(request, status=429, email_test_error=error)
    if not mail.enabled():
        error = "Save the email settings first."
        return _settings_page(request, status=400, email_test_error=error)
    sent = mail.send(
        to=admin.email,
        subject="Snug: a test email",
        text=(
            f"This is a test from Snug at {settings.CONFIG.origin}, sent because you asked "
            "for one in Settings. If it arrived, Snug can send email.\n"
        ),
    )
    if not sent:
        error = (
            "The test email could not be sent. Check the settings, and Snug's log for the "
            "kind of error: docker compose logs snug."
        )
        return _settings_page(request, status=502, email_test_error=error)
    return redirect(f"/admin/settings/?saved=email-test#{TEST_STEP}")


@never_cache
@login_required
@require_POST
def notifications_view(request: HttpRequest) -> HttpResponse:
    """Whether this admin is emailed the first time each link is opened."""
    admin = _admin(request)
    wanted = request.POST.get("first_open") == "on"
    if admin.notify_first_open != wanted:
        admin.notify_first_open = wanted
        admin.save(update_fields=["notify_first_open"])
        _audit(request, "admin.notifications_changed", admin.email, admin.email, first_open=wanted)
    return redirect("/admin/settings/?saved=notifications#notifications")


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
    return redirect("/admin/settings/?removed=1#admins")


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
            "To replace your own authenticator, use Replace your authenticator, under Your account."
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
    return redirect("/admin/settings/?reset=1#admins")


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
    if row.purpose == AdminSetup.Purpose.PASSWORD:
        return _new_password(request, row)

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


def _new_password(request: HttpRequest, row: AdminSetup) -> HttpResponse:
    """The forgotten-password link: a new password, proved with the second factor."""
    admin = row.admin

    def page(status: int = 200, **errors: str) -> HttpResponse:
        context = {"email": admin.email, "errors": errors}
        return render(request, "accounts/password_reset.html", context, status=status)

    if request.method == "GET":
        return page()
    if not ratelimit.allow(limits.SETUP_PER_IP, client_ip(request) or "unknown"):
        return page(429, code=TOO_MANY)

    # The password first: a bad one must not spend a code.
    password = request.POST.get("password", "")
    if password != request.POST.get("password2", ""):
        return page(400, password="The passwords do not match.")  # noqa: S106 - a message
    try:
        validate_password(password, user=admin)
    except ValidationError as problem:
        return page(400, password=" ".join(problem.messages))

    code = request.POST.get("code", "")
    if not ratelimit.allow(limits.RESET_CODE_PER_ADMIN, str(admin.pk)):
        return page(429, code=TOO_MANY)

    method = passwords.change(row, password, code)
    if method == "gone":
        return render(request, "accounts/setup_gone.html", status=404)
    if method in (None, "spent"):
        _audit(request, "admin.password_reset_failed", "", admin.email, reason="wrong_code")
        if method == "spent":
            return page(400, code="Too many wrong codes. Ask for a new link.")
        return page(400, code="That code is not right.")

    details: dict[str, Any] = {"method": method}
    if method == "recovery":
        details["recovery_codes_left"] = recovery.remaining(admin)
    _audit(request, "admin.password_reset", admin.email, admin.email, **details)
    passwords.send_changed(admin, how="with the forgotten-password link")
    context = {"codes_left": details.get("recovery_codes_left")}
    return render(request, "accounts/password_reset_done.html", context)
