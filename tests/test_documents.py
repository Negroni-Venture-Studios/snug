"""Uploading PDFs: what is accepted, what is refused, and where files go."""

import dataclasses
from pathlib import Path

import pytest
from django.conf import settings
from django.test import override_settings

from snug.accounts.models import Admin
from snug.documents import ingest as ingest_module
from snug.documents import storage
from snug.documents.ingest import UploadRejectedError, ingest
from snug.documents.models import Document, DocumentVersion
from snug.rendering import pages, sandbox
from tests.pdfs import chunks, make_pdf

pytestmark = pytest.mark.django_db

DECK = make_pdf((960, 540), (960, 540), (595, 842))


@pytest.fixture(autouse=True)
def own_data_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Files outlive the database rollback between tests, so each test gets its own."""
    for folder in ("originals", "renders", "tmp"):
        (tmp_path / folder).mkdir()
    monkeypatch.setattr(settings, "CONFIG", dataclasses.replace(settings.CONFIG, data_dir=tmp_path))


@pytest.fixture
def admin() -> Admin:
    return Admin.objects.create(email="ruth@fernlea.example", is_active=True)


@pytest.fixture
def document(admin: Admin) -> Document:
    return Document.objects.create(title="Seed deck", created_by=admin)


def upload(document: Document, data: bytes, filename: str = "deck.pdf") -> DocumentVersion:
    return ingest(document, chunks(data), filename=filename, uploaded_by=None)


def leftovers() -> list[Path]:
    return list(storage.tmp_dir().iterdir())


def test_a_pdf_becomes_version_one(document: Document) -> None:
    version = upload(document, DECK)
    assert version.number == 1
    assert version.page_count == 3
    assert version.page_sizes == [[960, 540], [960, 540], [595, 842]]
    assert version.size_bytes == len(DECK)
    assert len(version.sha256) == 64
    assert storage.original_path(version).read_bytes() == DECK
    assert leftovers() == []


def test_new_uploads_become_new_versions(document: Document) -> None:
    upload(document, DECK)
    second = upload(document, make_pdf((960, 540)))
    assert second.number == 2
    assert document.latest_version() == second


def test_files_are_stored_under_generated_names(document: Document) -> None:
    version = upload(document, DECK, filename="../../../etc/passwd.pdf")
    path = storage.original_path(version)
    assert path.parent == storage.originals_dir()
    assert path.name == version.stored_name
    assert "passwd" not in path.name
    # The uploaded name is kept for display only, without any directory part.
    assert version.original_filename == "passwd.pdf"


@pytest.mark.parametrize(
    "name", ["../x.pdf", "a/b.pdf", "x.pdf", "0" * 32 + ".exe", "/etc/passwd", ""]
)
def test_paths_come_only_from_generated_names(document: Document, name: str) -> None:
    version = upload(document, DECK)
    version.stored_name = name
    with pytest.raises(ValueError, match="generated name"):
        storage.original_path(version)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"", "empty"),
        (b"Hello, this is a Word document really", "not a PDF"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 100, "not a PDF"),
        (b"%PDF-1.7\n" + b"garbage " * 200, "could not be read"),
        (DECK[: len(DECK) // 3], "could not be read"),
    ],
    ids=["empty", "text", "png", "garbage-after-header", "truncated"],
)
def test_bad_files_are_refused_and_leave_nothing_behind(
    document: Document, data: bytes, message: str
) -> None:
    with pytest.raises(UploadRejectedError, match=message):
        upload(document, data)
    assert not DocumentVersion.objects.exists()
    assert leftovers() == []
    assert list(storage.originals_dir().iterdir()) == []


def test_uploads_over_the_limit_are_refused_while_streaming(document: Document) -> None:
    with (
        override_settings(MAX_UPLOAD_BYTES=len(DECK) - 1),
        pytest.raises(UploadRejectedError, match="larger than"),
    ):
        upload(document, DECK)
    assert leftovers() == []


def test_a_timeout_is_reported_kindly(document: Document, monkeypatch: pytest.MonkeyPatch) -> None:
    def too_slow(*args: str, timeout: float) -> dict[str, object]:
        raise sandbox.WorkerError("timeout")

    monkeypatch.setattr(sandbox, "run", too_slow)
    with pytest.raises(UploadRejectedError, match="took too long"):
        upload(document, DECK)
    assert leftovers() == []


def test_pages_render_to_cached_webp(document: Document, monkeypatch: pytest.MonkeyPatch) -> None:
    version = upload(document, DECK)
    path = pages.page_image(version, 1)
    assert path.read_bytes()[:4] == b"RIFF"
    assert path == storage.render_path(version, 1, 1600)

    calls: list[tuple[object, ...]] = []

    def spy(*args: object) -> bytes:
        calls.append(args)
        return b""

    monkeypatch.setattr(sandbox, "render", spy)
    assert pages.page_image(version, 1) == path
    assert calls == []  # served from the cache


def test_the_cache_can_be_cleared_and_rebuilt(document: Document) -> None:
    version = upload(document, DECK)
    first = pages.page_image(version, 2, width=800)
    storage.clear_render_cache(version)
    assert not first.exists()
    assert pages.page_image(version, 2, width=800).exists()
    storage.clear_render_cache()
    assert not storage.renders_dir().exists()


@pytest.mark.parametrize(("page", "width"), [(0, 1600), (4, 1600), (-1, 1600), (1, 1234)])
def test_render_requests_are_checked(document: Document, page: int, width: int) -> None:
    version = upload(document, DECK)
    with pytest.raises(ValueError, match=r"out of range|Unsupported"):
        pages.page_image(version, page, width)


def test_document_tokens_are_long_and_random(admin: Admin) -> None:
    tokens = {Document.objects.create(title=f"d{i}").token for i in range(20)}
    assert len(tokens) == 20
    assert all(len(t) >= 22 for t in tokens)


def test_unsaved_versions_have_no_render_path(document: Document) -> None:
    with pytest.raises(ValueError, match="not saved"):
        storage.render_path(DocumentVersion(page_count=1), 1, 1600)


def test_display_names_drop_directories() -> None:
    assert ingest_module._display_name("C:\\Users\\ruth\\Seed deck.pdf") == "Seed deck.pdf"
