"""Links: one per person, for one document (rooms arrive in step 6).

The token in a link's URL is 256 random bits, and only its SHA-256 hash is
stored. A leaked database or backup therefore exposes no working links. The
full link is shown once, when it is created or reissued.
"""

import hashlib
import secrets
from datetime import datetime

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


class Link(models.Model):
    # SHA-256 of the URL token. The token itself is never stored.
    token_hash = models.CharField(max_length=64, unique=True)
    # A short, non-secret reference for watermarks, emails and the audit log.
    ref = models.CharField(max_length=8, unique=True)
    email = models.EmailField()
    note = models.CharField(max_length=200, blank=True)
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="links")

    require_verification = models.BooleanField(default=True)
    allow_download = models.BooleanField(default=False)
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

    def __str__(self) -> str:
        return f"L-{self.ref} for {self.email}"

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
        return cls.objects.select_related("document").filter(token_hash=hash_token(token)).first()
