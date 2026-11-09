"""Snug's email transport, chosen when each message is sent, not at start-up.

Settings saved in the admin interface take effect at once, without a
restart. snug.core.mail.outgoing says which settings are in force.
"""

import smtplib
from collections.abc import Sequence
from typing import Any

from django.conf import settings
from django.core.mail import EmailMessage
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.filebased import EmailBackend as FileBackend
from django.core.mail.backends.smtp import EmailBackend as SmtpBackend

from snug.core import mail


class _PinnedSmtpBackend(SmtpBackend):
    """Django's SMTP backend, connecting to one checked address.

    smtplib opens its socket in _get_socket(host, ...), and keeps the server's
    name apart, for TLS: STARTTLS and SMTP_SSL both check the certificate
    against that name. Only the address the socket connects to changes.
    """

    def __init__(self, *, address: str, **options: Any) -> None:
        super().__init__(**options)
        self._address = address

    @property
    def connection_class(self) -> type[smtplib.SMTP]:
        base = smtplib.SMTP_SSL if self.use_ssl else smtplib.SMTP
        address = self._address

        class Pinned(base):  # type: ignore[valid-type, misc]
            def _get_socket(self, host: str, port: int, timeout: float) -> Any:
                return super()._get_socket(address, port, timeout)

        return Pinned


class EmailBackend(BaseEmailBackend):
    """Failures are always raised: snug.core.mail.send catches and reports them."""

    def send_messages(self, email_messages: Sequence[EmailMessage]) -> int:
        if not email_messages:
            return 0
        now = getattr(email_messages[0], "snug_outgoing", None) or mail.outgoing()
        # Given an alias, Django 6.1 takes the options below as they are, and never
        # falls back to its old EMAIL_* settings, which MAILERS removes. (The
        # attribute is new in 6.1, and the type stubs do not have it yet.)
        alias = getattr(self, "alias", None) or "default"
        if now.kind == "smtp":
            # Checked again here, in case the server's DNS has changed since it
            # was saved, and for the settings in snug.env, which nothing checks.
            refused, address = mail.vetted(now.host, now.port)
            if refused:
                raise mail.UnsafeServerError(f"The SMTP server is {refused}.")
            backend: BaseEmailBackend = _PinnedSmtpBackend(
                address=address,
                host=now.host,
                port=now.port,
                username=now.username,
                password=now.password,
                use_tls=now.security == "starttls",
                use_ssl=now.security == "tls",
                timeout=15,
                alias=alias,
            )
        elif now.kind == "outbox":
            backend = FileBackend(file_path=str(settings.CONFIG.data_dir / "outbox"), alias=alias)
        else:
            return 0
        return backend.send_messages(email_messages) or 0
