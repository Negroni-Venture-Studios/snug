"""The shared rate limiter: counts, windows, and no raw identities stored."""

import pytest

from snug.core import ratelimit
from snug.core.models import RateLimitWindow

pytestmark = pytest.mark.django_db

LIMIT = ratelimit.Limit("test", limit=3, window=60)
NOW = 1_800_000_000.0  # the start of a 60-second window


def test_counts_and_limits() -> None:
    assert not ratelimit.exceeded(LIMIT, "a", now=NOW)
    assert [ratelimit.hit(LIMIT, "a", now=NOW) for _ in range(3)] == [1, 2, 3]
    assert ratelimit.exceeded(LIMIT, "a", now=NOW)
    assert not ratelimit.exceeded(LIMIT, "b", now=NOW)


def test_a_new_window_starts_afresh_and_old_ones_are_removed() -> None:
    for _ in range(3):
        ratelimit.hit(LIMIT, "a", now=NOW)
    assert not ratelimit.exceeded(LIMIT, "a", now=NOW + 60)
    ratelimit.hit(LIMIT, "a", now=NOW + 60)
    assert RateLimitWindow.objects.count() == 1


def test_scopes_are_separate() -> None:
    other = ratelimit.Limit("other", limit=1, window=60)
    ratelimit.hit(other, "a", now=NOW)
    assert ratelimit.exceeded(other, "a", now=NOW)
    assert not ratelimit.exceeded(LIMIT, "a", now=NOW)


def test_identities_are_stored_hashed() -> None:
    ratelimit.hit(LIMIT, "ruth@fernlea.example", now=NOW)
    key = RateLimitWindow.objects.get().key
    assert len(key) == 64
    assert "ruth" not in key


def test_purge_removes_old_windows() -> None:
    ratelimit.hit(LIMIT, "a", now=NOW)
    ratelimit.hit(LIMIT, "b", now=NOW + 2 * 86400)
    assert ratelimit.purge_expired(now=NOW + 2 * 86400) == 1
    assert RateLimitWindow.objects.count() == 1


def test_default_clock() -> None:
    assert ratelimit.hit(LIMIT, "now") == 1
    assert ratelimit.purge_expired() == 0


def test_allow_counts_first_then_decides() -> None:
    # Counting before deciding means simultaneous requests cannot all see
    # "under the limit": each sees the count its own attempt produced.
    limit = ratelimit.Limit("test.allow", limit=2, window=60)
    assert [ratelimit.allow(limit, "a", now=NOW) for _ in range(3)] == [True, True, False]
    assert ratelimit.exceeded(limit, "a", now=NOW)
