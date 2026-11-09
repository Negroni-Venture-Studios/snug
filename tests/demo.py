"""A demo instance for the documentation's screenshots: Fernlea Robotics' data room.

Everything here is made up: the company, its documents, the investors who
read them and how long they spent on each page. The reading time per page
is the sample data from docs/design/build_mockups.py, so the screenshots
match the mockups.
"""

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from unittest import mock

from django.conf import settings
from django.utils import timezone

from snug.accounts.models import Admin
from snug.analytics.models import PageView, Visit
from snug.audit.models import AuditEvent
from snug.core.models import SiteSettings, SystemCheck
from snug.documents.ingest import ingest
from snug.documents.models import Document
from snug.links import service
from snug.links.models import Link
from snug.nda import terms
from snug.rooms.models import Folder, Room, RoomDocument
from tests.demo_deck import deck_pdf
from tests.pdfs import chunks, make_pdf
from tests.videos import CLIP
from tests.workbooks import make_xlsx

COMPANY = "Fernlea Robotics"
DOMAIN = "room.fernlea.example"
NDA = (
    "Everything in this data room is confidential. You agree to use it only to decide "
    "whether to invest in Fernlea Robotics Ltd, to share it with nobody outside your firm, "
    "and to delete your copies if you decide not to invest."
)


@dataclass
class Reader:
    email: str
    note: str
    visits: int
    ago: timedelta
    seconds: list[int]


# Seconds on each of the deck's 18 pages (0: never shown), from the mockups.
READERS = [
    Reader(
        "sam@kittiwake.example", "Kittiwake Partners", 1, timedelta(minutes=12), [24, 16] + [0] * 16
    ),
    Reader(
        "priya@northfield.example",
        "Partner, Northfield Capital",
        4,
        timedelta(hours=2),
        [38, 71, 52, 31, 162, 96, 58, 49, 171, 84, 33, 27, 88, 63, 104, 52, 29, 214],
    ),
    Reader(
        "tom@harbourcap.example",
        "Associate, Harbour Capital",
        3,
        timedelta(hours=19),
        [41, 66, 47, 28, 118, 73, 44, 39, 136, 61, 22, 19, 57, 41, 80, 36, 18, 120],
    ),
    Reader(
        "j.ortiz@example.com",
        "Adviser",
        3,
        timedelta(days=1, hours=6),
        [22, 47, 31, 19, 74, 52, 36, 30, 83, 47, 14, 12, 39, 26, 51, 21, 12, 54],
    ),
    Reader(
        "a.mensah@quarry.example",
        "Quarry Angels",
        2,
        timedelta(days=2, hours=3),
        [30, 52, 44, 27, 96, 61, 40, 33, 112, 48, 21, 14] + [0] * 6,
    ),
    Reader(
        "lena@foxglove.example",
        "Foxglove Ventures",
        1,
        timedelta(days=3, hours=1),
        [26, 31, 22, 13] + [0] * 14,
    ),
]


# Where each reader appears to be, from the documentation address ranges.
PLACES = {
    "sam@kittiwake.example": ("203.0.113.24", "GB"),
    "priya@northfield.example": ("198.51.100.17", "GB"),
    "tom@harbourcap.example": ("203.0.113.88", "GB"),
    "j.ortiz@example.com": ("198.51.100.140", "ES"),
    "a.mensah@quarry.example": ("203.0.113.201", "GB"),
    "lena@foxglove.example": ("198.51.100.66", "SE"),
}
BROWSER = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15"


def _event(at: datetime, action: str, **fields: Any) -> None:
    """An audit event at a moment in the past: its time is set as it is created."""
    with mock.patch("django.utils.timezone.now", return_value=at):
        AuditEvent.objects.create(action=action, **fields)


@dataclass
class Demo:
    deck: Document
    model: Document
    demo_video: Document
    room: Room
    links: dict[str, service.Issued]
    room_link: service.Issued
    model_link: service.Issued
    nda_link: service.Issued


def _visits(link: Link, document: Document, reader: Reader, now: datetime) -> None:
    """Reading time as if the reader had come back reader.visits times."""
    shown = [n for n, s in enumerate(reader.seconds, start=1) if s]
    for n in range(reader.visits):
        last = now - reader.ago - timedelta(days=n * 2)
        start = last - timedelta(minutes=20)
        visit = Visit.objects.create(
            token=secrets.token_urlsafe(16),
            link=link,
            email=reader.email,
            document=document,
            version_number=1,
            started_at=start,
            last_seen_at=last,
            current_page=shown[-1] if shown else 1,
            clock_at=last,
        )
        # The first visit holds most of the time; later ones return to a few pages.
        pages = shown if n == 0 else shown[:: n + 2]
        share = 0.7 if reader.visits > 1 and n == 0 else 0.3 / max(1, reader.visits - 1)
        for page in pages:
            seconds = reader.seconds[page - 1] * (share if reader.visits > 1 else 1)
            PageView.objects.create(visit=visit, page=page, seconds=seconds, first_seen_at=start)
    Link.objects.filter(pk=link.pk).update(
        first_opened_at=now - reader.ago - timedelta(days=2 * (reader.visits - 1))
    )


def build(admin: Admin) -> Demo:
    now = timezone.now()
    site = SiteSettings.load()
    site.company_name = COMPANY
    site.accent_colour = "#1f4d3a"  # Fernlea's field green, from its deck
    site.save()
    terms.publish(NDA, by=admin)

    deck = Document.objects.create(title="Seed deck", created_by=admin)
    ingest(deck, chunks(deck_pdf()), filename="Fernlea seed deck.pdf", uploaded_by=admin)
    model = Document.objects.create(
        title="Financial model",
        description=(
            "Our five-year model, from the robots we build to the cash we need. "
            "The Summary sheet has the figures from the deck; P&L and Cash flow show the working."
        ),
        created_by=admin,
    )
    ingest(
        model,
        chunks(
            make_xlsx(
                (
                    ("Summary", "visible"),
                    ("P&amp;L", "visible"),
                    ("Cash flow", "visible"),
                    ("Hiring plan", "visible"),
                    ("Workings", "hidden"),
                )
            )
        ),
        filename="Fernlea model v7.xlsx",
        uploaded_by=admin,
    )
    demo_video = Document.objects.create(title="Fern in the field", created_by=admin)
    ingest(demo_video, chunks(CLIP.read_bytes()), filename="fern-demo.mp4", uploaded_by=admin)
    articles = Document.objects.create(title="Articles of association", created_by=admin)
    ingest(
        articles, chunks(make_pdf(*[(595, 842)] * 9)), filename="articles.pdf", uploaded_by=admin
    )
    cap_table = Document.objects.create(title="Cap table", created_by=admin)
    ingest(
        cap_table,
        chunks(make_xlsx((("Cap table", "visible"), ("Option pool", "visible")))),
        filename="cap table.xlsx",
        uploaded_by=admin,
    )

    room = Room.objects.create(name="Seed round", require_nda=True, created_by=admin)
    folders = {
        name: Folder.objects.create(room=room, name=name)
        for name in ("Company", "Financials", "Legal")
    }
    for document, folder in (
        (deck, "Company"),
        (demo_video, "Company"),
        (model, "Financials"),
        (cap_table, "Financials"),
        (articles, "Legal"),
    ):
        RoomDocument.objects.create(room=room, document=document, folder=folders[folder])

    links = {}
    for reader in READERS:
        issued = service.create(
            document=deck,
            email=reader.email,
            note=reader.note,
            message="Ahead of Thursday's call. The traction numbers are on page 9."
            if "priya" in reader.email
            else "",
            expires_at=now + timedelta(days=30),
            require_verification=True,
            allow_download=True,
            created_by=admin,
        )
        links[reader.email] = issued
        _visits(issued.link, deck, reader, now)
        label = issued.link.label
        opened = now - reader.ago
        ip, country = PLACES[reader.email]
        viewer = {
            "actor_type": AuditEvent.ActorType.VIEWER,
            "actor": reader.email,
            "target": reader.email,
            "ip_address": ip,
            "country": country,
            "user_agent": BROWSER,
            "details": {"link": label, "document": deck.token},
        }
        _event(
            opened - timedelta(days=4),
            "link.created",
            actor_type=AuditEvent.ActorType.ADMIN,
            actor=admin.email,
            target=reader.email,
            ip_address="203.0.113.5",
            country="GB",
            user_agent=BROWSER,
            details={"link": label, "document": deck.token},
        )
        _event(opened - timedelta(minutes=22), "viewer.code_sent", **viewer)
        _event(opened - timedelta(minutes=21), "viewer.verified", **viewer)
        _event(opened - timedelta(minutes=20), "viewer.document_viewed", **viewer)
        if reader.visits > 2:
            _event(opened - timedelta(minutes=3), "viewer.downloaded", **viewer)

    room_link = service.create(
        room=room,
        email="d.ward@ashgrove.example",
        note="Ashgrove Capital",
        expires_at=None,
        require_verification=True,
        allow_download=True,
        created_by=admin,
    )
    model_link = service.create(
        document=model,
        email="priya@northfield.example",
        note="Partner, Northfield Capital",
        expires_at=None,
        require_verification=False,
        allow_download=True,
        created_by=admin,
    )
    nda_link = service.create(
        document=deck,
        email="new@birchwood.example",
        note="Birchwood Ventures",
        expires_at=None,
        require_verification=False,
        require_nda=True,
        allow_download=True,
        created_by=admin,
    )
    old = service.create(
        document=deck,
        email="m.chen@example.org",
        note="Met at a demo day",
        expires_at=None,
        require_verification=True,
        allow_download=False,
        created_by=admin,
    )
    service.revoke(old.link, admin)
    _event(
        now - timedelta(minutes=5),
        "admin.login",
        actor_type=AuditEvent.ActorType.ADMIN,
        actor=admin.email,
        ip_address="203.0.113.5",
        country="GB",
        user_agent=BROWSER,
    )

    # Housekeeping has run and the backups are current, so the Overview says all is well.
    SystemCheck.objects.update_or_create(
        name="housekeeping", defaults={"ok": True, "message": "", "checked_at": now}
    )
    backup = settings.CONFIG.data_dir / "backup"
    backup.mkdir(parents=True, exist_ok=True)
    (backup / "last-success").write_text((now - timedelta(minutes=2)).isoformat())
    return Demo(deck, model, demo_video, room, links, room_link, model_link, nda_link)
