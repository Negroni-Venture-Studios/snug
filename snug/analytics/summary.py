"""How a document was read: the figures behind the stat strip and the reading grid."""

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from snug.analytics.models import PageView, Visit
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

    @property
    def total_seconds(self) -> float:
        return sum(self.seconds)


@dataclass
class Person:
    email: str
    note: str = ""
    last_seen: datetime | None = None
    documents: list[PersonDocument] = field(default_factory=list)

    @property
    def total_seconds(self) -> float:
        return sum(d.total_seconds for d in self.documents)


def for_people() -> list[Person]:
    """Everyone who has opened anything, most recent first, with what they read."""
    visits = list(Visit.objects.select_related("link", "document").order_by("started_at"))
    if not visits:
        return []
    page_counts: dict[int, int] = {}
    for visit in visits:
        if visit.document_id not in page_counts:
            latest = visit.document.latest_version()
            page_counts[visit.document_id] = latest.page_count if latest else 0
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
            )
            person.documents.append(reads[key])
        read = reads[key]
        read.visits += 1
        read.last_seen = max(filter(None, [read.last_seen, visit.last_seen_at]))
        owner[visit.pk] = key
    for view in views:
        read = reads[owner[view["visit_id"]]]
        read.seconds[view["page"] - 1] += view["seconds"]
        read.seen[view["page"] - 1] = True

    for person in people.values():
        person.documents.sort(key=lambda d: d.last_seen or datetime.min, reverse=True)
    return sorted(people.values(), key=lambda p: p.last_seen or datetime.min, reverse=True)
