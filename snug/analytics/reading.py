"""Turning the reader's events into visits and time per page.

The reader page is served once per page, so "page shown" is recorded when it
is served. While the tab is visible, its script sends a heartbeat every
BEAT_SECONDS; when the tab is hidden or closed it sends "end", and when it is
shown again, "resume". Snug credits each heartbeat with the time that really
passed since the last one, at most MAX_CREDIT, so a browser cannot claim more
reading time than elapsed, and a sleeping laptop does not count as reading.
"""

import secrets
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from snug.analytics.models import PageView, Visit
from snug.documents.models import Document, DocumentVersion
from snug.viewer.access import Access

BEAT_SECONDS = 15
MAX_CREDIT = timedelta(seconds=BEAT_SECONDS + 5)
VISIT_GAP = timedelta(minutes=30)
# "part": a video has moved into another part (see PART_SECONDS), its "page".
KINDS = ("beat", "end", "resume", "part")


def _credit(visit: Visit, now: datetime) -> None:
    """Add the time since the clock last ran to the page being shown."""
    elapsed = min(max(now - visit.clock_at, timedelta(0)), MAX_CREDIT)
    if elapsed:
        PageView.objects.filter(visit=visit, page=visit.current_page).update(
            seconds=F("seconds") + elapsed.total_seconds()
        )


def page_shown(
    access: Access, document: Document, version: DocumentVersion, page: int
) -> tuple[Visit, bool]:
    """Record a page of a document being shown. Returns the visit, and whether it is new."""
    now = timezone.now()
    with transaction.atomic():
        visit = (
            Visit.objects.select_for_update()
            .filter(
                link=access.link,
                session=access.session,
                document=document,
                last_seen_at__gt=now - VISIT_GAP,
            )
            .first()
        )
        new = visit is None
        if visit is None:
            visit = Visit.objects.create(
                token=secrets.token_urlsafe(16),
                link=access.link,
                session=access.session,
                email=access.session.email,
                document=document,
                version_number=version.number,
                started_at=now,
                last_seen_at=now,
                current_page=page,
                clock_at=now,
            )
        else:
            # Turning the page: the page being left gets its time first.
            _credit(visit, now)
        PageView.objects.get_or_create(visit=visit, page=page, defaults={"first_seen_at": now})
        visit.current_page = page
        visit.clock_at = visit.last_seen_at = now
        visit.save(update_fields=["current_page", "clock_at", "last_seen_at"])
    return visit, new


def _move_to_part(visit: Visit, part: int, now: datetime) -> bool:
    """A video playing on into another part: the part being left gets its time first."""
    latest = visit.document.latest_version()
    if latest is None or not latest.is_video or not 1 <= part <= latest.page_count:
        return False
    if part != visit.current_page:
        _credit(visit, now)
        PageView.objects.get_or_create(visit=visit, page=part, defaults={"first_seen_at": now})
        visit.current_page = part
    visit.clock_at = visit.last_seen_at = now
    visit.save(update_fields=["current_page", "clock_at", "last_seen_at"])
    return True


def heartbeat(access: Access, token: str, page: int, kind: str) -> bool:
    """Credit a heartbeat to its visit. False if it belongs to no visit of this session."""
    now = timezone.now()
    with transaction.atomic():
        visit = (
            Visit.objects.select_for_update()
            .filter(token=token, link=access.link, session=access.session)
            .first()
        )
        if visit is None or kind not in KINDS:
            return False
        if kind == "part":
            return _move_to_part(visit, page, now)
        if page == visit.current_page and kind != "resume":
            _credit(visit, now)
        # "resume" restarts the clock without crediting the time the tab was hidden.
        visit.clock_at = visit.last_seen_at = now
        visit.save(update_fields=["clock_at", "last_seen_at"])
    return True
