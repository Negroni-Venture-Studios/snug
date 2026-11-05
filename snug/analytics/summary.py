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
