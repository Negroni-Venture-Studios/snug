"""Setup links: inviting admins, and confirming admin changes.

Decided 5 November 2026: an invited admin gets an emailed link that works
for 72 hours, and sets up their password and authenticator in the browser.
Inviting, removing or resetting an admin asks for a fresh code from the
acting admin's authenticator, and every admin is emailed about the change.
"""

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from snug.accounts import recovery, totp
from snug.accounts.models import Admin, AdminSetup
from snug.core import mail

SETUP_HOURS = 72
_MAX_TOKEN = 100


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def setup_url(token: str) -> str:
    return f"{settings.CONFIG.origin}/admin/setup/{token}"


def find(token: str) -> AdminSetup | None:
    """The live setup link for a token from a URL, or None."""
    if not token or len(token) > _MAX_TOKEN:
        return None
    return (
        AdminSetup.objects.select_related("admin")
        .filter(token_hash=_hash(token), expires_at__gt=timezone.now())
        .first()
    )


def issue(admin: Admin, purpose: str, by: Admin | None) -> str:
    """A new setup link for this admin, replacing any other. Returns the token, once."""
    token = secrets.token_urlsafe(32)
    with transaction.atomic():
        AdminSetup.objects.filter(admin=admin).delete()
        AdminSetup.objects.create(
            admin=admin,
            purpose=purpose,
            token_hash=_hash(token),
            created_by=by,
            expires_at=timezone.now() + timedelta(hours=SETUP_HOURS),
        )
    return token


def confirm(admin: Admin, code: str) -> bool:
    """Check a fresh code from the admin's own authenticator, once."""
    with transaction.atomic():
        # Re-read inside the write transaction, so a code cannot confirm twice.
        fresh = Admin.objects.get(pk=admin.pk)
        step = totp.verify(fresh.totp_secret, code, last_step=fresh.totp_last_step)
        if step is None:
            return False
        fresh.totp_last_step = step
        fresh.save(update_fields=["totp_last_step"])
    return True


def invitable(email: str) -> Admin | None:
    """The account to invite: new, or an earlier invite not yet taken up.

    Returns None if the address already belongs to a working admin.
    """
    existing = Admin.objects.filter(email=email).first()
    if existing is None:
        account = Admin(email=email, is_active=False)
        account.set_unusable_password()
        return account
    if existing.is_active:
        return None
    return existing


def complete(row: AdminSetup, *, password: str | None, secret: str, step: int) -> list[str]:
    """Finish setting up: save the password and authenticator, return recovery codes."""
    with transaction.atomic():
        admin = Admin.objects.select_for_update().get(pk=row.admin_id)
        if password is not None:
            admin.set_password(password)
        admin.is_active = True
        admin.totp_secret = secret
        admin.totp_last_step = step
        admin.totp_confirmed_at = timezone.now()
        admin.save()
        codes = recovery.issue(admin)
        row.delete()
    return codes


def send_invite(admin: Admin, token: str, by: Admin) -> bool:
    company = settings.CONFIG.company_name
    context = {
        "company": company,
        "by": by.email,
        "url": setup_url(token),
        "hours": SETUP_HOURS,
    }
    return mail.send(
        to=admin.email,
        subject=f"You are invited to {company or 'a Snug data room'}",
        text=render_to_string("accounts/email/invite.txt", context),
        html=render_to_string("accounts/email/invite.html", context),
        reply_to=by.email,
    )


def alert_admins(*, subject: str, what: str) -> None:
    """Tell every working admin about a change to the admins. Best effort."""
    company = settings.CONFIG.company_name
    context = {"what": what, "company": company, "origin": settings.CONFIG.origin}
    text = render_to_string("accounts/email/alert.txt", context)
    html = render_to_string("accounts/email/alert.html", context)
    for email in Admin.objects.filter(is_active=True).values_list("email", flat=True):
        mail.send(to=email, subject=f"{company or 'Snug'}: {subject}", text=text, html=html)
