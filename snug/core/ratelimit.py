"""A small rate limiter kept in SQLite, so every server process shares it.

Fixed windows: a key may be hit `limit` times in each `window` seconds.
Callers check `exceeded()` before doing expensive or sensitive work, and call
`hit()` for each attempt they want counted.
"""

import hashlib
import time
from dataclasses import dataclass

from django.db import transaction
from django.db.models import F

from snug.core.models import RateLimitWindow


@dataclass(frozen=True)
class Limit:
    scope: str
    limit: int
    window: int  # seconds

    def key(self, identity: str) -> str:
        return hashlib.sha256(f"{self.scope}\x00{identity}".encode()).hexdigest()

    def _start(self, now: float | None) -> int:
        moment = int(time.time() if now is None else now)
        return moment - moment % self.window


def hit(limit: Limit, identity: str, now: float | None = None) -> int:
    """Count one attempt. Returns the number of attempts in this window."""
    key, start = limit.key(identity), limit._start(now)
    with transaction.atomic():
        row, created = RateLimitWindow.objects.get_or_create(
            key=key, window_start=start, defaults={"count": 1}
        )
        if not created:
            RateLimitWindow.objects.filter(pk=row.pk).update(count=F("count") + 1)
            row.refresh_from_db(fields=["count"])
        # Earlier windows for this key can never matter again.
        RateLimitWindow.objects.filter(key=key, window_start__lt=start).delete()
    return row.count


def exceeded(limit: Limit, identity: str, now: float | None = None) -> bool:
    """True if this identity has already used up this window's attempts."""
    count = (
        RateLimitWindow.objects.filter(key=limit.key(identity), window_start=limit._start(now))
        .values_list("count", flat=True)
        .first()
    )
    return (count or 0) >= limit.limit


def purge_expired(older_than: int = 24 * 60 * 60, now: float | None = None) -> int:
    """Remove windows that ended long ago. Returns how many were removed."""
    cutoff = int(time.time() if now is None else now) - older_than
    deleted, _ = RateLimitWindow.objects.filter(window_start__lt=cutoff).delete()
    return deleted
