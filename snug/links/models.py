"""Links: one per person, for one document or one room.

The token in a link's URL is 256 random bits, and only its SHA-256 hash is
stored. A leaked database or backup therefore exposes no working links. The
full link is shown once, when it is created or reissued.
"""

import hashlib
import secrets
from datetime import date, datetime, timedelta

from django.conf import settings
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


class Link(models.Model):
    # SHA-256 of the URL token. The token itself is never stored.
    token_hash = models.CharField(max_length=64, unique=True)
    # A short, non-secret reference for watermarks, emails and the audit log.
    ref = models.CharField(max_length=8, unique=True)
    email = models.EmailField()
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
        return f"L-{self.ref} for {self.email}"

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
