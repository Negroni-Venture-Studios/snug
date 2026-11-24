"""The weekly digest: a summary of the last seven days' reading, emailed on Monday mornings.

Each admin turns it on under Your account in Settings; it is off until they
do. Housekeeping, which runs every hour, calls `send_due`. An admin is due
once the week that began at 08:00 on the most recent Monday, in the
instance's time zone, has had no digest for them. If the server was down on
Monday, the digest goes at the next housekeeping. Each admin is claimed with
one conditional update before sending, so two runs at once send one digest.
A week with nothing to report is claimed and sends nothing, and a digest
that fails to send is unclaimed, so the next housekeeping tries again.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.db.models import Q, Sum
from django.template.loader import render_to_string
from django.utils import timezone

from snug.accounts.models import Admin
from snug.analytics.charts import duration
from snug.analytics.models import PageView
from snug.audit.models import AuditEvent
from snug.core import mail
from snug.core.models import company_name
from snug.links.models import Link

DAYS = 7
SEND_AT = time(8, 0)  # Monday, in the instance's time zone


def week_start(now: datetime) -> datetime:
    """08:00 on the most recent Monday, at or before now, in the instance's time zone."""
    local = timezone.localtime(now)
    monday = local.date() - timedelta(days=local.weekday())
    start = timezone.make_aware(datetime.combine(monday, SEND_AT))
    return start if start <= now else start - timedelta(days=DAYS)


def _day(day: date, *, year: bool) -> str:
    return f"{day.day} {day:%B %Y}" if year else f"{day.day} {day:%B}"


def period(start: datetime, end: datetime) -> str:
    """ "18 to 25 November 2026", with a month or a year only where they differ."""
    first, last = timezone.localdate(start), timezone.localdate(end)
    if first.year != last.year:
        return f"{_day(first, year=True)} to {_day(last, year=True)}"
    if first.month != last.month:
        return f"{_day(first, year=False)} to {_day(last, year=True)}"
    return f"{first.day} to {_day(last, year=True)}"


@dataclass
class Reader:
    email: str
    note: str
    seconds: float

    @property
    def time(self) -> str:
        return duration(self.seconds)


@dataclass
class DocumentWeek:
    title: str
    readers: list[Reader] = field(default_factory=list)

    @property
    def seconds(self) -> float:
        return sum(reader.seconds for reader in self.readers)

    @property
    def time(self) -> str:
        return duration(self.seconds)


@dataclass
class Week:
    start: datetime
    end: datetime
    documents: list[DocumentWeek]
    first_opens: list[Link]
    downloads: int
    expiring: list[Link]

    @property
    def people(self) -> int:
        return len({reader.email for document in self.documents for reader in document.readers})

    @property
    def time(self) -> str:
        return duration(sum(document.seconds for document in self.documents))

    @property
    def empty(self) -> bool:
        return not (self.documents or self.first_opens or self.downloads or self.expiring)


def build(now: datetime) -> Week:
    """What happened in the seven days to now, and the links that end in the next seven."""
    start = now - timedelta(days=DAYS)
    rows = (
        PageView.objects.filter(first_seen_at__gte=start, first_seen_at__lt=now)
        .values("visit__document__title", "visit__document_id", "visit__email", "visit__link__note")
        .annotate(seconds=Sum("seconds"))
    )
    by_document: dict[int, DocumentWeek] = {}
    readers: dict[tuple[int, str], Reader] = {}
    for row in rows:
        document = by_document.setdefault(
            row["visit__document_id"], DocumentWeek(title=row["visit__document__title"])
        )
        key = (row["visit__document_id"], row["visit__email"])
        if key not in readers:
            readers[key] = Reader(row["visit__email"], row["visit__link__note"] or "", 0.0)
            document.readers.append(readers[key])
        readers[key].seconds += row["seconds"] or 0.0
    documents = sorted(by_document.values(), key=lambda d: (-d.seconds, d.title))
    for document in documents:
        document.readers.sort(key=lambda r: (-r.seconds, r.email))

    links = Link.objects.select_related("document", "room")
    expiring = links.filter(
        revoked_at__isnull=True, expires_at__gt=now, expires_at__lte=now + timedelta(days=DAYS)
    ).order_by("expires_at")
    return Week(
        start=start,
        end=now,
        documents=documents,
        first_opens=list(
            links.filter(first_opened_at__gte=start, first_opened_at__lt=now).order_by(
                "first_opened_at"
            )
        ),
        downloads=AuditEvent.objects.filter(
            action="viewer.downloaded", at__gte=start, at__lt=now
        ).count(),
        expiring=list(expiring),
    )


def _claim(admin: Admin, now: datetime) -> bool:
    due = Q(digest_sent_at__isnull=True) | Q(digest_sent_at__lt=week_start(now))
    return Admin.objects.filter(due, pk=admin.pk).update(digest_sent_at=now) == 1


def send_due(now: datetime) -> int:
    """Send this week's digest to each admin who wants it and has not had it. Returns how many."""
    if not mail.enabled():
        return 0  # nothing is claimed, so it goes once email works
    due = Admin.objects.filter(is_active=True, notify_weekly_digest=True).filter(
        Q(digest_sent_at__isnull=True) | Q(digest_sent_at__lt=week_start(now))
    )
    week: Week | None = None
    sent = 0
    for admin in due:
        before = admin.digest_sent_at
        if not _claim(admin, now):
            continue
        week = week or build(now)
        if week.empty:
            continue
        company = company_name()
        context = {
            "week": week,
            "company": company,
            "period": period(week.start, week.end),
            "people_url": f"{settings.CONFIG.origin}/admin/people/",
            "settings_url": f"{settings.CONFIG.origin}/admin/settings/#notifications",
        }
        if mail.send(
            to=admin.email,
            subject=f"{company or 'Snug'}: your week, {context['period']}",
            text=render_to_string("analytics/email/digest.txt", context),
            html=render_to_string("analytics/email/digest.html", context),
        ):
            sent += 1
        else:  # tried again at the next housekeeping
            Admin.objects.filter(pk=admin.pk, digest_sent_at=now).update(digest_sent_at=before)
    return sent
