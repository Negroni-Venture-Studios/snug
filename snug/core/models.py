from django.db import models


class RateLimitWindow(models.Model):
    """How many times one key was hit in one fixed time window.

    Keys are SHA-256 hashes, so the table never holds an email address or an
    IP address in clear.
    """

    key = models.CharField(max_length=64)
    window_start = models.BigIntegerField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=("key", "window_start"), name="ratelimit_key_window"),
        )

    def __str__(self) -> str:
        return f"{self.key[:12]} @ {self.window_start}: {self.count}"


class SiteSettings(models.Model):
    """Instance-wide settings an admin changes in the interface. One row, always pk 1."""

    # A link to the founder's own privacy policy, shown to viewers (UK GDPR:
    # the founder is the controller of viewers' data). Only plain https URLs.
    privacy_url = models.URLField(max_length=500, blank=True)

    class Meta:
        verbose_name_plural = "site settings"

    def __str__(self) -> str:
        return "Site settings"

    @classmethod
    def load(cls) -> "SiteSettings":
        row, _ = cls.objects.get_or_create(pk=1)
        return row
