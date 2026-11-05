"""NDA versions, and acceptances of them. Acceptances are never changed or deleted."""

from typing import Any

from django.conf import settings
from django.db import models

from snug.links.models import Link


class NdaVersion(models.Model):
    """One version of the instance's NDA text. The newest is the current one."""

    number = models.PositiveIntegerField(unique=True)
    text = models.TextField()
    sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ("-number",)

    def __str__(self) -> str:
        return f"NDA version {self.number}"


class NdaAcceptance(models.Model):
    """A record of acceptance, not a signature: who, which link, which text, when, from where."""

    link = models.ForeignKey(Link, on_delete=models.PROTECT, related_name="nda_acceptances")
    version = models.ForeignKey(NdaVersion, on_delete=models.PROTECT, related_name="acceptances")
    name = models.CharField(max_length=200)
    email = models.EmailField()
    accepted_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    # The hash of the exact text accepted, kept here as well as on the version.
    text_sha256 = models.CharField(max_length=64)

    class Meta:
        ordering = ("-accepted_at",)

    def __str__(self) -> str:
        return f"{self.name} accepted version {self.version_id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValueError("An NDA acceptance is a record: it is never changed.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValueError("An NDA acceptance is a record: it is never deleted.")
