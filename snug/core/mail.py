"""Sending email. Failures are reported to the caller, never logged with content."""

import logging
import smtplib
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import connections
from django.utils import timezone

from snug.core.models import EmailSettings, SystemCheck

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Outgoing:
    """How email leaves Snug right now. Never shown whole: it holds the password."""

    kind: str  # "smtp", "outbox" (localhost only, for development) or "off"
    source: str = ""  # "settings" or "snug.env", for "smtp"
    from_address: str = ""
    host: str = ""
    port: int = 0
    username: str = ""
    password: str = field(default="", repr=False)
    security: str = "starttls"


def outgoing() -> Outgoing:
    """What is in force: the settings saved in Settings, else snug.env, else the
    outbox on localhost, else nothing."""
    row = EmailSettings.load()
    if row.provider == "smtp":
        return Outgoing(
            kind="smtp",
            source="settings",
            from_address=row.from_address,
            host=row.smtp_host,
            port=row.smtp_port or 587,
            username=row.smtp_username,
            password=row.smtp_password,
            security=row.smtp_security or "starttls",
        )
    config = settings.CONFIG
    if config.smtp_host:
        return Outgoing(
            kind="smtp",
            source="snug.env",
            from_address=config.email_from,
            host=config.smtp_host,
            port=config.smtp_port,
            username=config.smtp_username,
            password=config.smtp_password,
            security=config.smtp_security,
        )
    if config.email_outbox:
        return Outgoing(kind="outbox", from_address=config.email_from or "snug@localhost")
    return Outgoing(kind="off")


def enabled() -> bool:
    return outgoing().kind != "off"


def send(
    *,
    to: str,
    subject: str,
    text: str,
    html: str = "",
    reply_to: str = "",
    via: Outgoing | None = None,
) -> bool:
    """Send one message. Returns False if it could not be sent.

    Messages carry links and codes, so on failure only the kind of error is
    logged: never the recipient, the subject or the body.
    """
    # `via` is read on the request thread by callers that send from another one,
    # which must not touch the database.
    now = via or outgoing()
    if now.kind == "off":
        return False
    message = EmailMultiAlternatives(
        subject=subject,
        body=text,
        from_email=now.from_address or None,
        to=[to],
        reply_to=[reply_to] if reply_to else None,
    )
    if html:
        message.attach_alternative(html, "text/html")
    # The transport sends with these settings, without reading them again.
    message.snug_outgoing = now  # type: ignore[attr-defined]
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
