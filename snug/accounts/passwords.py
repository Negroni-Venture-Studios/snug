"""Forgotten passwords: an emailed link that works once, for one hour.

Asked for 5 November 2026. Decided 6 November 2026: choosing the new password
also needs a code from the authenticator, or a recovery code, so someone in
the admin's mailbox cannot change it. A new password signs the admin out
everywhere, because each session holds a hash of it, and they are emailed.
"""

import re

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from snug.accounts import limits, recovery, setup
from snug.accounts.models import Admin, AdminSetup
from snug.core import mail

LINK_HOURS = 1
_TOTP = re.compile(r"^\s*\d{6}\s*$")


def resettable(email: str) -> Admin | None:
    """The admin a link may go to: working, with an authenticator. None for anyone else."""
    return Admin.objects.filter(
        email=email, is_active=True, totp_confirmed_at__isnull=False
    ).first()


def issue(admin: Admin) -> str:
    return setup.issue(admin, AdminSetup.Purpose.PASSWORD, by=None, hours=LINK_HOURS)


def send_link(admin: Admin, token: str) -> bool:
    company = settings.CONFIG.company_name
    context = {"email": admin.email, "company": company, "url": setup.setup_url(token)}
    return mail.send(
        to=admin.email,
        subject="Choose a new password for Snug",
        text=render_to_string("accounts/email/password_link.txt", context),
        html=render_to_string("accounts/email/password_link.html", context),
    )


def send_changed(admin: Admin, *, how: str) -> bool:
    context = {
        "email": admin.email,
        "how": how,
        "company": settings.CONFIG.company_name,
        "origin": settings.CONFIG.origin,
        "when": timezone.localtime().strftime("%-d %B %Y at %H:%M"),
    }
    return mail.send(
        to=admin.email,
        subject="Your Snug password was changed",
        text=render_to_string("accounts/email/password_changed.txt", context),
        html=render_to_string("accounts/email/password_changed.html", context),
    )


def is_app_code(code: str) -> bool:
    """Six digits is a code from the app; anything else is tried as a recovery code."""
    return bool(_TOTP.match(code))


def change(row: AdminSetup, password: str, code: str) -> str | None:
    """Spend the link and the code, and set the new password.

    Returns how the second factor was proved ("totp" or "recovery"); "gone" if
    the link was used or expired meanwhile; "spent" if this wrong code was the
    last allowed, which deletes the link; or None for any other wrong code,
    which counts it on the link and leaves the link working. A wrong code
    spends no code. SQLite runs transactions in IMMEDIATE mode, so parallel
    attempts are handled one at a time.
    """
    # Hashed before the transaction: argon2 is slow on purpose.
    hashed = make_password(password)
    with transaction.atomic():
        link = (
            AdminSetup.objects.select_for_update()
            .filter(pk=row.pk, expires_at__gt=timezone.now())
            .first()
        )
        if link is None:
            return "gone"
        admin = Admin.objects.get(pk=link.admin_id)
        if is_app_code(code):
            method, ok = "totp", setup.confirm(admin, code)
        else:
            method, ok = "recovery", recovery.use(admin, code)
        if not ok:
            link.failures += 1
            if link.failures >= limits.RESET_CODE_FAILURES_PER_LINK:
                link.delete()
                return "spent"
            link.save(update_fields=["failures"])
            return None
        link.delete()  # the link works once
        Admin.objects.filter(pk=admin.pk).update(password=hashed)
    return method
