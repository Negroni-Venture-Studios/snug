"""How a document was read: the figures behind the stat strip and the reading grid."""

import hashlib
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from django.db.models import Count, Exists, OuterRef, Sum

from snug.analytics.models import PageView, Visit
from snug.audit.models import AuditEvent
from snug.documents.models import Document


@dataclass
class ViewerReading:
    email: str
    note: str
    pages: int
    visits: int = 0
    last_seen: datetime | None = None
    seconds: list[float] = field(default_factory=list)
    seen: list[bool] = field(default_factory=list)

    @property
    def total_seconds(self) -> float:
        return sum(self.seconds)

    @property
    def read_all(self) -> bool:
        return all(self.seen)


@dataclass
class DocumentReading:
    pages: int
    viewers: list[ViewerReading]
    visits: int
    median_seconds: int
    read_all: int
    averages: list[float]

    @property
    def viewer_count(self) -> int:
        return len(self.viewers)

    def longest(self, count: int = 2) -> list[tuple[int, float]]:
        ranked = sorted(range(self.pages), key=lambda i: self.averages[i], reverse=True)
        return [(i + 1, self.averages[i]) for i in ranked[:count] if self.averages[i] > 0]


def for_document(document: Document) -> DocumentReading | None:
    """The reading of every viewer of this document, or None if nobody has opened it."""
    visits = list(Visit.objects.filter(document=document).select_related("link"))
    if not visits:
        return None
    latest = document.latest_version()
    views = list(
        PageView.objects.filter(visit__document=document).values("visit_id", "page", "seconds")
    )
    pages = max([latest.page_count if latest else 0, *(v["page"] for v in views)])

    viewers: dict[str, ViewerReading] = {}
    owner: dict[int, str] = {}
    for visit in sorted(visits, key=lambda v: v.started_at):
        row = viewers.setdefault(
            visit.email,
            ViewerReading(visit.email, "", pages, seconds=[0.0] * pages, seen=[False] * pages),
        )
        row.visits += 1
        row.note = visit.link.note or row.note  # the most recent link's note
        row.last_seen = max(filter(None, [row.last_seen, visit.last_seen_at]))
        owner[visit.pk] = visit.email
    for view in views:
        row = viewers[owner[view["visit_id"]]]
        row.seconds[view["page"] - 1] += view["seconds"]
        row.seen[view["page"] - 1] = True

    readers = sorted(viewers.values(), key=lambda r: r.total_seconds, reverse=True)
    reached: dict[int, list[float]] = defaultdict(list)
    for row in readers:
        for i in range(pages):
            if row.seen[i]:
                reached[i].append(row.seconds[i])
    averages = [sum(reached[i]) / len(reached[i]) if reached[i] else 0.0 for i in range(pages)]
    return DocumentReading(
        pages=pages,
        viewers=readers,
        visits=len(visits),
        median_seconds=round(statistics.median(r.total_seconds for r in readers)),
        read_all=sum(1 for r in readers if r.read_all),
        averages=averages,
    )


@dataclass
class PersonDocument:
    title: str
    token: str
    pages: int
    visits: int = 0
    last_seen: datetime | None = None
    seconds: list[float] = field(default_factory=list)
    seen: list[bool] = field(default_factory=list)
    downloads: int = 0
    # A video's columns are its parts, labelled by time.
    video: bool = False
    # A workbook has one column: time on its page.
    workbook: bool = False

    @property
    def total_seconds(self) -> float:
        return sum(self.seconds)


def anchor(email: str) -> str:
    """The People page's anchor for a person: a hash, so no address is in a URL."""
    return "person-" + hashlib.sha256(email.casefold().encode()).hexdigest()[:12]


@dataclass
class Person:
    email: str
    note: str = ""
    last_seen: datetime | None = None
    documents: list[PersonDocument] = field(default_factory=list)

    @property
    def anchor(self) -> str:
        return anchor(self.email)

    @property
    def total_seconds(self) -> float:
        return sum(d.total_seconds for d in self.documents)

    @property
    def downloads(self) -> int:
        return sum(d.downloads for d in self.documents)


def for_people() -> list[Person]:
    """Everyone who has opened anything, most recent first, with what they read."""
    visits = list(Visit.objects.select_related("link", "document").order_by("started_at"))
    if not visits:
        return []
    page_counts: dict[int, int] = {}
    videos: set[int] = set()
    workbooks: set[int] = set()
    for visit in visits:
        if visit.document_id not in page_counts:
            latest = visit.document.latest_version()
            page_counts[visit.document_id] = latest.page_count if latest else 0
            if latest is not None and latest.is_video:
                videos.add(visit.document_id)
            if latest is not None and latest.is_workbook:
                # Its card is its one page; visits from before that was counted have none.
                workbooks.add(visit.document_id)
                page_counts[visit.document_id] = 1
    views = list(PageView.objects.values("visit_id", "page", "seconds"))
    document_of = {visit.pk: visit.document_id for visit in visits}
    for view in views:
        document_id = document_of[view["visit_id"]]
        page_counts[document_id] = max(page_counts[document_id], view["page"])

    people: dict[str, Person] = {}
    reads: dict[tuple[str, int], PersonDocument] = {}
    owner: dict[int, tuple[str, int]] = {}
    for visit in visits:
        person = people.setdefault(visit.email, Person(visit.email))
        person.note = visit.link.note or person.note
        person.last_seen = max(filter(None, [person.last_seen, visit.last_seen_at]))
        key = (visit.email, visit.document_id)
        pages = page_counts[visit.document_id]
        if key not in reads:
            reads[key] = PersonDocument(
                visit.document.title,
                visit.document.token,
                pages,
                seconds=[0.0] * pages,
                seen=[False] * pages,
                video=visit.document_id in videos,
                workbook=visit.document_id in workbooks,
            )
            person.documents.append(reads[key])
        read = reads[key]
        if read.workbook:
            read.seen[0] = True  # every visit to a workbook showed its card
        read.visits += 1
        read.last_seen = max(filter(None, [read.last_seen, visit.last_seen_at]))
        owner[visit.pk] = key
    for view in views:
        read = reads[owner[view["visit_id"]]]
        read.seconds[view["page"] - 1] += view["seconds"]
        read.seen[view["page"] - 1] = True

    # Downloads live in the audit log, which outlasts reading data: count only
    # those that match a document the person is shown reading.
    token_of = {visit.document.token: visit.document_id for visit in visits}
    downloads = (
        AuditEvent.objects.filter(action="viewer.downloaded")
        .values("actor", "details__document")
        .annotate(n=Count("id"))
    )
    for row in downloads:
        read_id = token_of.get(row["details__document"])
        if read_id is not None and (row["actor"], read_id) in reads:
            reads[(row["actor"], read_id)].downloads = int(row["n"] or 0)

    for person in people.values():
        person.documents.sort(key=lambda d: d.last_seen or datetime.min, reverse=True)
    return sorted(people.values(), key=lambda p: p.last_seen or datetime.min, reverse=True)


@dataclass
class RecentVisit:
    email: str
    document: Document
    room_name: str | None
    started_at: datetime
    seconds: float
    first: bool  # this person's first visit to this document


def recent_visits(limit: int = 10) -> list[RecentVisit]:
    """The latest visits for the Overview, each marked if it was a first open."""
    earlier = Visit.objects.filter(
        email=OuterRef("email"),
        document=OuterRef("document"),
        started_at__lt=OuterRef("started_at"),
    )
    visits = (
        Visit.objects.select_related("document", "link__room")
        .annotate(seconds=Sum("pages__seconds"), seen_before=Exists(earlier))
        .order_by("-started_at")[:limit]
    )
    return [
        RecentVisit(
            email=visit.email,
            document=visit.document,
            room_name=visit.link.room.name if visit.link.room else None,
            started_at=visit.started_at,
            seconds=visit.seconds or 0,
            first=not visit.seen_before,
        )
        for visit in visits
    ]
