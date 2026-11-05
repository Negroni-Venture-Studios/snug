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
