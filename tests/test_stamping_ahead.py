"""Stamped pages: stamped ahead while an investor opens a link, and kept until the day ends.

A stamp took 2 to 3 seconds on a one-CPU server (12 November 2026), and an
investor saw a blank page meanwhile. The link says whose it is, so the first
pages are stamped while the investor is on the landing page. Every page
stamped is kept for the rest of the day, keyed by link, version, page, width
and the stamp's own text, and every request still goes through authorise()
before one is served.
"""

import fcntl
import io
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from snug.audit.models import AuditEvent
from snug.documents import storage
from snug.documents.ingest import ingest
from snug.documents.models import Document, DocumentVersion
from snug.links import service
from snug.rendering import pages, sandbox
from snug.rooms.models import Room, RoomDocument
from snug.viewer import sessions
from tests.pdfs import chunks, make_pdf

pytestmark = pytest.mark.django_db

INVESTOR = "priya@northfield.example"
OTHER = "sam@eastgate.example"


@pytest.fixture
def stamps(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every stamp the worker is asked for, answered with a stand-in image."""
    asked: list[dict[str, Any]] = []

    def stamp(page: Path, font: Path, **text: str) -> bytes:
        asked.append({"page": Path(page).name, **text})
        return b"RIFF\x00\x00\x00\x00WEBP" + repr(text).encode()

    monkeypatch.setattr(sandbox, "stamp", stamp)
    return asked


def document(title: str = "Seed deck", pages_: int = 3) -> Document:
    deck = Document.objects.create(title=title)
    pdf = make_pdf(*[(960, 540)] * pages_)
    ingest(deck, chunks(pdf), filename="deck.pdf", uploaded_by=None)
    return deck


def link_to(email: str = INVESTOR, **target: Any) -> service.Issued:
    return service.create(
        email=email,
        note="",
        expires_at=None,
        require_verification=False,
        allow_download=True,
        created_by=None,
        **target,
    )


def opened(issued: service.Issued) -> Client:
    browser = Client()
    response = browser.post(f"/v/{issued.token}/open", secure=True)
    assert sessions.COOKIE in response.cookies
    return browser


def page(browser: Client, issued: service.Issued, number: int = 1, width: int = 1600) -> Any:
    return browser.get(f"/v/{issued.token}/pages/{number}-{width}.webp", secure=True)


def stamped_files(day: date | None = None) -> list[Path]:
    folder = storage.stamped_dir() / (day or timezone.localdate()).isoformat()
    return sorted(folder.rglob("*.webp")) if folder.exists() else []


def version_of(deck: Document) -> DocumentVersion:
    version = deck.latest_version()
    assert version is not None
    return version


# ---------------------------------------------------------------- kept for the day


def test_a_stamped_page_is_kept_for_the_rest_of_the_day(
    data_folder: Path, stamps: list[dict[str, Any]]
) -> None:
    issued = link_to(document=document())
    browser = opened(issued)
    first, again = page(browser, issued), page(browser, issued)
    assert first.status_code == again.status_code == 200
    assert first.content == again.content
    assert len(stamps) == 1
    today = timezone.localdate()
    assert stamps[0]["date"] == f"{today.day} {today:%b %Y}"
    assert stamps[0]["email"] == INVESTOR
    (kept,) = stamped_files()
    assert kept.parent.name == str(issued.link.pk)


def test_each_investor_has_their_own_stamped_pages(
    data_folder: Path, stamps: list[dict[str, Any]]
) -> None:
    deck = document()
    priya, sam = link_to(document=deck), link_to(OTHER, document=deck)
    assert page(opened(priya), priya).content != page(opened(sam), sam).content
    assert [stamp["email"] for stamp in stamps] == [INVESTOR, OTHER]
    assert len(stamped_files()) == 2


def test_the_next_day_the_page_is_stamped_again(
    data_folder: Path, stamps: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    issued = link_to(document=document())
    browser = opened(issued)
    page(browser, issued)
    tomorrow = timezone.localdate() + timedelta(days=1)
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: tomorrow)
    page(browser, issued)
    assert len(stamps) == 2
    assert stamps[1]["date"] == f"{tomorrow.day} {tomorrow:%b %Y}"


def test_a_page_being_stamped_is_waited_for_not_stamped_twice(
    data_folder: Path, stamps: list[dict[str, Any]]
) -> None:
    issued = link_to(document=document())
    version = version_of(issued.link.document)  # type: ignore[arg-type]
    today = timezone.localdate()
    target = pages.stamped_path(issued.link, version, 1, 1600, day=today)
    target.parent.mkdir(parents=True, exist_ok=True)
    got: list[bytes] = []
    with target.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # as a stamp in progress holds it
        reader = threading.Thread(
            target=lambda: got.append(
                pages.stamped_page(version, 1, 1600, link=issued.link, email=INVESTOR, day=today)
            )
        )
        reader.start()
        time.sleep(0.2)
        assert reader.is_alive() and not got  # waiting for the stamp in progress
        target.write_bytes(b"RIFF\x00\x00\x00\x00WEBPfinished")
        fcntl.flock(lock, fcntl.LOCK_UN)
    reader.join(timeout=10)
    assert got == [b"RIFF\x00\x00\x00\x00WEBPfinished"]
    assert stamps == []


def test_a_revoked_link_loses_its_stamped_pages(
    data_folder: Path, stamps: list[dict[str, Any]]
) -> None:
    deck = document()
    issued, other = link_to(document=deck), link_to(OTHER, document=deck)
    browser = opened(issued)
    page(browser, issued)
    page(opened(other), other)
    service.revoke(issued.link, by=None)
    assert page(browser, issued).status_code == 404
    assert [path.parent.name for path in stamped_files()] == [str(other.link.pk)]


def test_housekeeping_clears_other_days(data_folder: Path, stamps: list[dict[str, Any]]) -> None:
    issued = link_to(document=document())
    page(opened(issued), issued)
    yesterday = storage.stamped_dir() / (timezone.localdate() - timedelta(days=1)).isoformat()
    (yesterday / "7").mkdir(parents=True)
    (yesterday / "7" / "1-1-1600-0123456789abcdef.webp").write_bytes(b"old")
    call_command("housekeeping", stdout=io.StringIO())
    assert not yesterday.exists()
    assert len(stamped_files()) == 1


def test_the_store_is_built_from_records_only(data_folder: Path) -> None:
    issued = link_to(document=document())
    version = version_of(issued.link.document)  # type: ignore[arg-type]
    today = timezone.localdate()
    with pytest.raises(ValueError, match="out of range"):
        pages.stamped_path(issued.link, version, 4, 1600, day=today)
    with pytest.raises(ValueError, match="width"):
        pages.stamped_path(issued.link, version, 1, 1234, day=today)
    path = pages.stamped_path(issued.link, version, 1, 1600, day=today)
    assert path.is_relative_to(storage.stamped_dir() / today.isoformat() / str(issued.link.pk))
    # Nothing of the investor's address is in the name.
    assert "priya" not in str(path) and "northfield" not in str(path)


# ---------------------------------------------------------------- stamped ahead


@pytest.mark.usefixtures("background_now")
def test_the_landing_page_stamps_the_first_pages_ahead(
    data_folder: Path, stamps: list[dict[str, Any]]
) -> None:
    issued = link_to(document=document())
    landing = Client().get(f"/v/{issued.token}", secure=True)
    assert landing.status_code == 200
    expected = [("1-1600.webp", INVESTOR), ("2-1600.webp", INVESTOR)]
    assert [(s["page"], s["email"]) for s in stamps] == expected
    # Opening the link then reads them from the store: no stamp to wait for.
    browser = opened(issued)
    assert page(browser, issued, 1).status_code == page(browser, issued, 2).status_code == 200
    assert len(stamps) == 2


@pytest.mark.usefixtures("background_now")
def test_stamping_ahead_counts_as_nothing(data_folder: Path, stamps: list[dict[str, Any]]) -> None:
    issued = link_to(document=document())
    Client().get(f"/v/{issued.token}", secure=True)  # an email scanner, say
    assert stamps
    assert not AuditEvent.objects.filter(action="viewer.document_viewed").exists()
    assert issued.link.visits.count() == 0


@pytest.mark.usefixtures("background_now")
def test_a_one_page_document_is_stamped_once_ahead(
    data_folder: Path, stamps: list[dict[str, Any]]
) -> None:
    issued = link_to(document=document(pages_=1))
    Client().get(f"/v/{issued.token}", secure=True)
    assert [s["page"] for s in stamps] == ["1-1600.webp"]


@pytest.mark.usefixtures("background_now")
def test_a_room_link_stamps_the_first_page_of_its_first_documents(
    data_folder: Path, stamps: list[dict[str, Any]]
) -> None:
    room = Room.objects.create(name="Seed round")
    for title in ("Deck", "Financials", "Cap table", "Team", "Board pack"):
        RoomDocument.objects.create(room=room, document=document(title))
    issued = link_to(room=room)
    Client().get(f"/v/{issued.token}", secure=True)
    assert len(stamps) == pages.AHEAD_DOCUMENTS
    assert {s["page"] for s in stamps} == {"1-1600.webp"}


@pytest.mark.usefixtures("background_now")
def test_stamping_ahead_twice_stamps_once(data_folder: Path, stamps: list[dict[str, Any]]) -> None:
    issued = link_to(document=document())
    for _ in range(3):
        Client().get(f"/v/{issued.token}", secure=True)
    assert len(stamps) == 2


@pytest.mark.usefixtures("background_now")
def test_a_failed_stamp_ahead_is_left_for_the_reader(
    data_folder: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def refuse(*args: Any, **kwargs: Any) -> bytes:
        raise sandbox.WorkerError("timeout")

    monkeypatch.setattr(sandbox, "stamp", refuse)
    issued = link_to(document=document())
    assert Client().get(f"/v/{issued.token}", secure=True).status_code == 200
    assert stamped_files() == []
    assert "Could not stamp ahead" in caplog.text


def test_without_the_background_task_the_landing_page_waits_for_nothing(
    data_folder: Path, stamps: list[dict[str, Any]]
) -> None:
    # conftest.py stops background work by default: the page does not stamp inline.
    issued = link_to(document=document())
    assert Client().get(f"/v/{issued.token}", secure=True).status_code == 200
    assert stamps == []
