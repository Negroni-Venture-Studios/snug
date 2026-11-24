"""Links: for one document or one room, and for the people it lists.

A link lists who may open it: email addresses, and whole domains such as
fund.com, which admit any address there (issue #174). Each person still
verifies their own address and reads under it. A link for one address is a
list of one, and its `email` is that address.

The token in a link's URL is 256 random bits, and only its SHA-256 hash is
stored. A leaked database or backup therefore exposes no working links. The
full link is shown once, when it is created or reissued.
"""

import hashlib
import re
import secrets
from datetime import date, datetime, timedelta
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import models
from django.utils import timezone

from snug.documents.models import Document

# Unambiguous characters for the short reference: no 0/O, 1/I/L.
_REF_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def new_ref() -> str:
    return "".join(secrets.choice(_REF_ALPHABET) for _ in range(4))


# The longest cover message, in characters.
MAX_MESSAGE = 1000


# A plain address: one @, and nothing a mail header could read as a second
# recipient, a comment, or a quoted part (spaces, commas, quotes, brackets).
_PLAIN_ADDRESS = re.compile(r"[^\s@\"(),:;<>\[\]\\]+@[a-z0-9]([a-z0-9.-]*[a-z0-9])?")


def plain_address(email: str) -> str:
    """The address in lower case if it is a plain one, as codes are sent to, else ""."""
    address = email.strip().casefold()
    if not _PLAIN_ADDRESS.fullmatch(address) or ".." in address:
        return ""
    try:
        validate_email(address)
    except ValidationError:
        return ""
    return address


def domain_of(email: str) -> str:
    return email.rpartition("@")[2].casefold()


def is_domain(entry: str) -> bool:
    """An entry is a domain, such as fund.com, or else an email address."""
    return "@" not in entry


class Link(models.Model):
    # SHA-256 of the URL token. The token itself is never stored.
    token_hash = models.CharField(max_length=64, unique=True)
    # A short, non-secret reference for watermarks, emails and the audit log.
    ref = models.CharField(max_length=8, unique=True)
    # Who may open it, one entry per line, in lower case: an email address,
    # or a domain such as fund.com, which admits any address there and only
    # there (subdomains are entries of their own).
    entries = models.TextField()
    # The address, for a link to one person, and empty for any other; kept
    # from `entries` on saving.
    email = models.EmailField(blank=True)
    note = models.CharField(max_length=200, blank=True)
    # The cover message: plain text from the founder, shown to the investor.
    message = models.TextField(blank=True)
    # Exactly one of these: the link shares a document, or a room.
    document = models.ForeignKey(
        Document, null=True, blank=True, on_delete=models.CASCADE, related_name="links"
    )
    room = models.ForeignKey(
        "rooms.Room", null=True, blank=True, on_delete=models.CASCADE, related_name="links"
    )

    require_verification = models.BooleanField(default=True)
    # For a link to a single document. A room link follows its room's setting.
    require_nda = models.BooleanField(default=False)
    allow_download = models.BooleanField(default=True)
    # Opens only from the UK (snug.viewer.access). A room link follows its room instead.
    uk_only = models.BooleanField(default=False)
    expires_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reissued_at = models.DateTimeField(null=True, blank=True)
    # When this link first showed anyone a document: claimed once, for the
    # first-open email (snug.analytics.notify).
    first_opened_at = models.DateTimeField(null=True, blank=True)
    # Until when a wrong code gets the neutral reply: as long as a code sent
    # in neutral mode could still be live (snug.viewer.views.verify_code).
    neutral_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = (
            models.CheckConstraint(
                condition=(
                    models.Q(document__isnull=False, room__isnull=True)
                    | models.Q(document__isnull=True, room__isnull=False)
                ),
                name="link_has_one_target",
            ),
        )

    def __str__(self) -> str:
        return f"L-{self.ref} for {self.recipient}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        listed = self.entry_list
        self.email = listed[0] if len(listed) == 1 and not is_domain(listed[0]) else ""
        if "update_fields" in kwargs and "entries" in kwargs["update_fields"]:
            kwargs["update_fields"] = [*kwargs["update_fields"], "email"]
        super().save(*args, **kwargs)

    @property
    def entry_list(self) -> list[str]:
        return [line for line in self.entries.split() if line]

    @property
    def recipient(self) -> str:
        """Who the link is for, as admins read it: "priya@northfield.example", or "fund.com"."""
        return ", ".join(self.entry_list)

    @property
    def addresses(self) -> list[str]:
        """The single addresses it lists, which can be emailed the link."""
        return [entry for entry in self.entry_list if not is_domain(entry)]

    @property
    def domains(self) -> list[str]:
        return [entry for entry in self.entry_list if is_domain(entry)]

    @property
    def for_one_person(self) -> bool:
        return bool(self.email)

    @property
    def has_domain(self) -> bool:
        return any(is_domain(entry) for entry in self.entry_list)

    def listed(self, email: str) -> bool:
        """Whether one of the link's entries admits this address, blocked or not."""
        # Only a plain address is compared, so the domain checked is the one
        # mail is delivered to: "x@evil.example, y@fund.com" admits nobody.
        address = plain_address(email)
        if not address:
            return False
        for entry in self.entry_list:
            if address == entry or (is_domain(entry) and domain_of(address) == entry):
                return True
        return False

    def admits(self, email: str) -> bool:
        """Whether this address may open the link: listed, and not blocked on it."""
        return self.listed(email) and not (
            self.pk is not None
            and LinkPerson.objects.filter(
                link=self, email=plain_address(email), blocked_at__isnull=False
            ).exists()
        )

    @property
    def target_title(self) -> str:
        return self.room.name if self.room is not None else self._document().title

    @property
    def target_token(self) -> str:
        return self.room.token if self.room is not None else self._document().token

    def target_details(self) -> dict[str, str]:
        """For the audit log: which document or room the link shares."""
        return (
            {"room": self.target_token}
            if self.room is not None
            else {"document": self.target_token}
        )

    def target_url(self) -> str:
        """The admin page of what the link shares."""
        from django.urls import reverse

        if self.room is not None:
            return reverse("rooms:detail", kwargs={"token": self.room.token})
        return reverse("documents:detail", kwargs={"token": self._document().token})

    def _document(self) -> Document:
        if self.document is None:  # pragma: no cover - the database refuses it
            raise ValueError("A link shares a document or a room.")
        return self.document

    @property
    def label(self) -> str:
        return f"L-{self.ref}"

    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def is_expired(self, now: datetime | None = None) -> bool:
        return self.expires_at is not None and self.expires_at <= (now or timezone.now())

    def is_active(self, now: datetime | None = None) -> bool:
        return not self.is_revoked() and not self.is_expired(now)

    @property
    def last_day(self) -> date | None:
        """The last day the link works, in the instance's time zone.

        An expiry is stored as midnight at the start of the next day, so the
        date to show people is the day before that moment.
        """
        if self.expires_at is None:
            return None
        return timezone.localtime(self.expires_at - timedelta(microseconds=1)).date()

    @property
    def status(self) -> str:
        if self.is_revoked():
            return "revoked"
        if self.is_expired():
            return "expired"
        return "active"

    def set_new_token(self) -> str:
        """Give the link a fresh token, which stops the old one. Returns it, once."""
        token = new_token()
        self.token_hash = hash_token(token)
        return token

    @classmethod
    def find(cls, token: str) -> "Link | None":
        """The link for a token from a URL, whatever its state, or None."""
        if not token or len(token) > 100:
            return None
        return (
            cls.objects.select_related("document", "room")
            .filter(token_hash=hash_token(token))
            .first()
        )


class LinkPerson(models.Model):
    """One person on a link: someone who opened it, or whom an admin blocked on it.

    Their first open is claimed here, once, for the first-open email. A blocked
    address is refused by `Link.admits`, so their session ends at once, while
    everyone else on the link carries on.
    """

    link = models.ForeignKey(Link, on_delete=models.CASCADE, related_name="people")
    # In lower case, as `Link.admits` compares it.
    email = models.EmailField()
    first_opened_at = models.DateTimeField(null=True, blank=True)
    blocked_at = models.DateTimeField(null=True, blank=True)
    blocked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ("email",)
        constraints = (models.UniqueConstraint(fields=("link", "email"), name="link_person_once"),)

    def __str__(self) -> str:
        return f"{self.email} on {self.link.label}"
