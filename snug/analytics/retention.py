"""Deleting reading data some months after its link ends.

Decided 5 November 2026: visits and time per page are kept while a link
works, and for reading_retention_months (12 by default, set in Settings)
after it is revoked or expires. The link itself, and the audit log, stay.
"""

import calendar
from datetime import datetime

from django.db.models import Q
from django.utils import timezone

from snug.analytics.models import Visit
from snug.core.models import SiteSettings

MAX_MONTHS = 120


def months_before(moment: datetime, months: int) -> datetime:
    """The same day and time, so many calendar months earlier (clamped to month end)."""
    month_index = moment.year * 12 + moment.month - 1 - months
    year, month = divmod(month_index, 12)
    day = min(moment.day, calendar.monthrange(year, month + 1)[1])
    return moment.replace(year=year, month=month + 1, day=day)


def purge(now: datetime | None = None) -> int:
    """Delete reading data for links that ended before the retention period. Returns visits."""
    months = SiteSettings.load().reading_retention_months
    cutoff = months_before(now or timezone.now(), months)
    ended = Q(link__revoked_at__lt=cutoff) | Q(link__expires_at__lt=cutoff)
    visits = Visit.objects.filter(ended)
    count = visits.count()
    if count:
        visits.delete()  # page views go with them
    return count
