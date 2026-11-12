"""Page 1 is rendered once a version can be read: the first investor waits only for its watermark.

Pages are rendered on first view and cached, then stamped for each viewer.
On a one-CPU server the first view of page 1 took 11 seconds, and an
investor saw a blank page meanwhile (12 November 2026).
"""

import threading
import time
from pathlib import Path
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from snug.accounts import sessions
from snug.accounts.models import Admin
from snug.documents import storage
from snug.documents.ingest import ingest
from snug.documents.models import Document, DocumentVersion
from snug.office import previews
from snug.rendering import pages, sandbox
from tests.fake_converter import Converter
from tests.office_files import make_pptx
from tests.pdfs import chunks, make_pdf

pytestmark = pytest.mark.django_db

# Taken before conftest.py replaces it for each test.
REAL_IN_BACKGROUND = pages._in_background

DECK = make_pdf((960, 540), (960, 540))


@pytest.fixture
def warmed(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The versions asked to be warmed, in place of the background thread."""
    asked: list[int] = []
    monkeypatch.setattr(pages, "warm_in_background", lambda version: asked.append(version.pk))
    return asked


def upload(data: bytes = DECK, name: str = "deck.pdf") -> DocumentVersion:
    deck = Document.objects.create(title="Deck")
    return ingest(deck, chunks(data), filename=name, uploaded_by=None)


def first_page(version: DocumentVersion) -> Path:
    return storage.render_path(version, 1, pages.DEFAULT_WIDTH)


def test_warming_renders_the_first_page(data_folder: Path) -> None:
    version = upload()
    assert not first_page(version).exists()
    pages.warm(version)
    assert first_page(version).exists()


def test_warming_a_file_without_pages_renders_nothing(converter: Converter) -> None:
    version = upload(make_pptx(3), "deck.pptx")
    pages.warm(version)  # its preview is not ready, so it has no pages yet
    assert not (storage.renders_dir() / str(version.pk)).exists()


def test_a_page_that_cannot_be_rendered_is_left_for_the_viewer(
    data_folder: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    version = upload()

    def refuse(*args: Any, **kwargs: Any) -> bytes:
        raise sandbox.WorkerError("timeout")

    monkeypatch.setattr(sandbox, "render", refuse)
    pages.warm(version)
    assert not first_page(version).exists()
    assert "Could not render the first page" in caplog.text


@pytest.mark.usefixtures("background_now")
def test_the_background_task_renders_the_first_page(data_folder: Path) -> None:
    version = upload()
    pages.warm_in_background(version)
    assert first_page(version).exists()


def test_background_work_runs_in_a_thread_of_its_own() -> None:
    # Every test but this one stops the threads (conftest.py): they would outlive it.
    ran: list[str] = []
    thread = REAL_IN_BACKGROUND(lambda: ran.append(threading.current_thread().name))
    assert thread is not None
    thread.join(timeout=10)
    assert ran and ran[0] != threading.current_thread().name


def test_a_file_that_has_gone_is_left_for_the_viewer(
    data_folder: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A new version can replace an Office file's preview while page 1 renders.
    version = upload()
    storage.original_path(version).unlink()
    pages.warm(version)
    assert "Could not render the first page" in caplog.text


def test_a_pdf_upload_warms_its_first_page(
    data_folder: Path, warmed: list[int], django_capture_on_commit_callbacks: Any
) -> None:
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    browser = Client(REMOTE_ADDR="198.51.100.7")
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    with django_capture_on_commit_callbacks(execute=True):
        browser.post(
            "/admin/documents/new",
            {"title": "Seed deck", "pdf": SimpleUploadedFile("deck.pdf", DECK)},
            secure=True,
        )
    version = DocumentVersion.objects.get()
    assert warmed == [version.pk]


def test_a_ready_preview_warms_its_first_page(converter: Converter, warmed: list[int]) -> None:
    version = upload(make_pptx(4), "deck.pptx")
    previews.pump()
    assert warmed == []
    converter.answer(make_pdf(*[(720, 540)] * 4))
    previews.pump()
    assert warmed == [version.pk]


def test_a_failed_preview_warms_nothing(converter: Converter, warmed: list[int]) -> None:
    upload(make_pptx(4), "deck.pptx")
    previews.pump()
    converter.answer(error="timeout")
    previews.pump()
    assert warmed == []
