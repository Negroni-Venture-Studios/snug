"""Viewer sessions and verification codes. Neither is stored in a usable form.

A viewer's browser holds a random token in a cookie; the database keeps only
its SHA-256 hash. A verification code is kept only as an HMAC keyed with the
secret key, so a copy of the database alone cannot recover a code.
"""

from django.db import models

from snug.links.models import Link


class ViewerSession(models.Model):
    """One browser's access to one link, for one email address.

    A browser holds one cookie, which may carry sessions for several links:
    each link is verified, revoked and audited on its own.
    """

    token_hash = models.CharField(max_length=64)
    link = models.ForeignKey(Link, on_delete=models.CASCADE, related_name="viewer_sessions")
    email = models.EmailField()
    # False only for links that do not require verification.
    verified = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=("token_hash", "link"), name="one_session_per_link"),
        )

    def __str__(self) -> str:
        return f"Session for {self.email} on {self.link.label}"


class VerificationCode(models.Model):
    """The live six-digit code for a link. A new code replaces the old one."""

    link = models.ForeignKey(Link, on_delete=models.CASCADE, related_name="+")
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    failures = models.PositiveSmallIntegerField(default=0)

    def __str__(self) -> str:
        return f"Code for {self.link.label}"
