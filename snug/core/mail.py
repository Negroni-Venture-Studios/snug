"""Sending email. Failures are reported to the caller, never logged with content."""

import ipaddress
import logging
import smtplib
import socket
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

    kind: str  # "smtp", "google", "outbox" (localhost only, for development) or "off"
    source: str = ""  # "settings" or "snug.env", for "smtp"
    from_address: str = ""
    host: str = ""
    port: int = 0
    username: str = ""
    password: str = field(default="", repr=False)
    security: str = "starttls"
    client_id: str = ""
    client_secret: str = field(default="", repr=False)
    refresh_token: str = field(default="", repr=False)


def outgoing() -> Outgoing:
    """What is in force: the settings saved in Settings, else snug.env, else the
    outbox on localhost, else nothing."""
    row = EmailSettings.load()
    # Google counts once a mailbox is connected; until then, what came before.
    if row.provider == "google" and row.google_refresh_token:
        return Outgoing(
            kind="google",
            source="settings",
            from_address=row.google_account,
            host="Google Workspace",
            client_id=row.google_client_id,
            client_secret=row.google_client_secret,
            refresh_token=row.google_refresh_token,
        )
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


# The ports mail providers accept submissions on. Anything else could aim Snug's
# SMTP client at some other service.
SMTP_PORTS = (25, 465, 587, 2525)


class UnsafeServerError(OSError):
    """The SMTP server resolves to an address Snug will not connect to."""


def _addresses(host: str, port: int) -> list[str]:
    return [str(info[4][0]) for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


# IPv6 ranges that carry an IPv4 address inside, which Python's is_global can
# pass while the IPv4 address within is private: NAT64 (global, and local-use),
# 6to4 and Teredo. Refused outright.
_TRANSLATED = tuple(
    ipaddress.ip_network(network)
    for network in ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32")
)


def _allowed(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return _allowed(str(ip.ipv4_mapped))
        if any(ip in network for network in _TRANSLATED):
            return False
    return ip.is_global


def vetted(host: str, port: int = 587) -> tuple[str, str]:
    """Whether Snug may connect to this SMTP server, and the address to connect to.

    Returns ("", address) if allowed, else ("private", "") or ("unknown", "").
    A stolen admin session could otherwise aim Snug at a service on the
    server's own network, or at the cloud provider's metadata address. The
    transport connects to the address checked here, so a name that resolves
    differently a moment later cannot slip past (DNS rebinding). On
    localhost, for development, anything goes.
    """
    if settings.CONFIG.domain == "localhost":
        return "", host
    try:
        found = _addresses(host, port)
    except (OSError, UnicodeError, ValueError):
        return "unknown", ""
    if not found:
        return "unknown", ""
    if not all(_allowed(address) for address in found):
        return "private", ""
    return "", found[0].split("%", 1)[0]


def public_server(host: str, port: int = 587) -> str:
    """ "" if Snug may connect to this SMTP server; otherwise "private" or "unknown"."""
    return vetted(host, port)[0]


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
