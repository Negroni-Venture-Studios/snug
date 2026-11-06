"""Sending email. Failures are reported to the caller, never logged with content."""

import logging
import smtplib
import threading
from collections.abc import Callable

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import connections
from django.utils import timezone

from snug.core.models import SystemCheck

log = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(settings.CONFIG.email_enabled)


def send(*, to: str, subject: str, text: str, html: str = "", reply_to: str = "") -> bool:
    """Send one message. Returns False if it could not be sent.

    Messages carry links and codes, so on failure only the kind of error is
    logged: never the recipient, the subject or the body.
    """
    if not enabled():
        return False
    message = EmailMultiAlternatives(
        subject=subject,
        body=text,
        to=[to],
        reply_to=[reply_to] if reply_to else None,
    )
    if html:
        message.attach_alternative(html, "text/html")
    try:
        message.send(fail_silently=False)
    except (smtplib.SMTPException, OSError) as error:
        log.warning("Email could not be sent: %s", type(error).__name__)
        _record(
            ok=False,
            message=(
                f"An email could not be sent ({type(error).__name__}). "
                "Run snug doctor --email you@example.com to test."
            ),
        )
        return False
    _record(ok=True, message="The last email was sent.")
    return True


def _record(*, ok: bool, message: str) -> None:
    """For the Overview's status line: whether the last email went. Never who or what.

    Written only when that changes, so most sends only read. A note about
    email must never stop one being sent, so any error here is only logged.
    """
    try:
        current = SystemCheck.objects.filter(name="email").first()
        if current is not None and current.ok == ok and current.message == message:
            return
        SystemCheck.objects.update_or_create(
            name="email", defaults={"ok": ok, "message": message, "checked_at": timezone.now()}
        )
    except Exception as error:  # noqa: BLE001 - never let the note stop an email
        log.warning("Could not record whether email works: %s", type(error).__name__)


def in_background(task: Callable[[], object]) -> None:
    """Send without making the reply wait, so it takes as long as one that sends nothing.

    The task may use the database; the thread closes its own connection after.
    """

    def run() -> None:
        try:
            task()
        finally:
            connections.close_all()

    threading.Thread(target=run, daemon=True).start()
