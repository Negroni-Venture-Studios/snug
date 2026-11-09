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
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from snug.accounts import recovery, totp
from snug.accounts.models import Admin, AdminSetup, RecoveryCode
from snug.core import mail
from snug.core.models import company_name

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


def new_token() -> str:
    return secrets.token_urlsafe(32)


def issue(
    admin: Admin,
    purpose: str,
    by: Admin | None,
    token: str | None = None,
    *,
    hours: int = SETUP_HOURS,
) -> str:
    """A new setup link for this admin, replacing any other. Returns the token, once."""
    token = token or new_token()
    with transaction.atomic():
        AdminSetup.objects.filter(admin=admin).delete()
        AdminSetup.objects.create(
            admin=admin,
            purpose=purpose,
            token_hash=_hash(token),
            created_by=by,
            expires_at=timezone.now() + timedelta(hours=hours),
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


def complete(row: AdminSetup, *, password: str | None, secret: str, step: int) -> list[str] | None:
    """Finish setting up: save the password and authenticator, return recovery codes.

    Returns None, changing nothing, if another request used the link first.
    """
    # Hashed before the transaction: argon2 is slow on purpose, and the
    # database should not wait for it.
    hashed = make_password(password) if password is not None else None
    with transaction.atomic():
        # Deleting the link is the claim: of two requests racing on it, only
        # one deletes the row, and only that one changes the account.
        claimed, _ = AdminSetup.objects.filter(pk=row.pk, expires_at__gt=timezone.now()).delete()
        if not claimed:
            return None
        admin = Admin.objects.get(pk=row.admin_id)
        if hashed is not None:
            admin.password = hashed
        admin.is_active = True
        admin.totp_secret = secret
        admin.totp_last_step = step
        admin.totp_confirmed_at = timezone.now()
        admin.save()
        return recovery.issue(admin)


def remove(target: Admin) -> bool:
    """Remove an admin. Refuses, changing nothing, if no working admin would remain.

    The check follows the delete in one transaction, so two admins removing
    each other at the same moment cannot leave the instance with none.
    """
    with transaction.atomic():
        Admin.objects.filter(pk=target.pk).delete()
        if not Admin.objects.filter(is_active=True).exists():
            transaction.set_rollback(True)
            return False
    return True


def reset_second_factor(target: Admin, token: str, by: Admin) -> None:
    """Clear an admin's authenticator and recovery codes, and give them a setup link.

    Clearing the key also signs them out everywhere, because each session
    holds a hash of it.
    """
    with transaction.atomic():
        Admin.objects.filter(pk=target.pk).update(
            totp_secret="", totp_confirmed_at=None, totp_last_step=0
        )
        RecoveryCode.objects.filter(admin=target).delete()
        issue(target, AdminSetup.Purpose.RESET, by, token=token)


def send_reset(admin: Admin, token: str, by: Admin) -> bool:
    context = {"by": by.email, "url": setup_url(token), "hours": SETUP_HOURS}
    return mail.send(
        to=admin.email,
        subject="Set up your authenticator for Snug again",
        text=render_to_string("accounts/email/reset.txt", context),
        html=render_to_string("accounts/email/reset.html", context),
        reply_to=by.email,
    )


def send_replace(admin: Admin, token: str) -> bool:
    """The setup link for an admin replacing their own authenticator, in case they close the tab."""
    context = {
        "email": admin.email,
        "company": company_name(),
        "url": setup_url(token),
        "hours": SETUP_HOURS,
    }
    return mail.send(
        to=admin.email,
        subject="Finish setting up your new authenticator for Snug",
        text=render_to_string("accounts/email/replace.txt", context),
        html=render_to_string("accounts/email/replace.html", context),
    )


def send_invite(admin: Admin, token: str, by: Admin) -> bool:
    company = company_name()
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


def alert_admins(
    *, subject: str, what: str, also: str = "", via: mail.Outgoing | None = None
) -> None:
    """Tell every working admin about a change to the admins. Best effort.

    `also` adds one more address, such as an admin who has just been removed.
    `via` sends through particular settings, such as the email settings in force
    before a change, so whoever made it cannot swallow the alert.
    """
    company = company_name()
    context = {"what": what, "company": company, "origin": settings.CONFIG.origin}
    text = render_to_string("accounts/email/alert.txt", context)
    html = render_to_string("accounts/email/alert.html", context)
    recipients = set(Admin.objects.filter(is_active=True).values_list("email", flat=True))
    if also:
        recipients.add(also)
    for email in sorted(recipients):
        mail.send(
            to=email, subject=f"{company or 'Snug'}: {subject}", text=text, html=html, via=via
        )
