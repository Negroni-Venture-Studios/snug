"""Snug's side of the LibreOffice converter: the queue, the answers, and new versions waiting.

Decided 12 November 2026. Snug sends the converter one file at a time,
through an inbox it writes and the converter only reads, and collects the
PDF from an outbox. The PDF is read by Snug's own confined worker before it
becomes the version's preview. A new version of a document waits for its
preview before links show it, so an investor reading pages never drops back
to a download card; the first version of a new document is shared at once.
"""

import dataclasses
import fcntl
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.test import Client
from django.utils import timezone

from snug.analytics import summary
from snug.documents.ingest import ingest
from snug.documents.models import OFFICE, Document, DocumentVersion
from snug.links import service
from snug.office import previews
from tests.fake_converter import Converter
from tests.office_files import make_docx, make_pptx
from tests.pdfs import chunks, make_pdf
from tests.test_ooxml import custom_properties

pytestmark = pytest.mark.django_db

Preview = DocumentVersion.Preview


def upload(document: Document, data: bytes, name: str = "Board deck.pptx") -> DocumentVersion:
    return ingest(document, chunks(data), filename=name, uploaded_by=None)


def fresh(version: DocumentVersion) -> DocumentVersion:
    return DocumentVersion.objects.get(pk=version.pk)


# ---------------------------------------------------------------- the queue


def test_an_office_upload_waits_in_snugs_queue_until_the_queue_runs(converter: Converter) -> None:
    version = upload(Document.objects.create(title="Board deck"), make_pptx(3))
    assert version.preview == Preview.PENDING
    assert version.status == DocumentVersion.Status.READY  # a first version is shared at once
    assert converter.jobs() == []


def test_the_queue_hands_the_converter_the_file_and_its_kind(converter: Converter) -> None:
    data = make_pptx(3)
    version = upload(Document.objects.create(title="Board deck"), data)
    previews.pump()
    (job,) = converter.jobs()
    assert json.loads((job / "job.json").read_text()) == {"kind": "pptx"}
    assert (job / "input.pptx").read_bytes() == data
    assert fresh(version).preview == Preview.CONVERTING


def test_the_converter_gets_one_file_at_a_time(converter: Converter) -> None:
    first = upload(Document.objects.create(title="Board deck"), make_pptx(3))
    second = upload(Document.objects.create(title="Board memo"), make_docx(), "Memo.docx")
    previews.pump()
    previews.pump()
    assert len(converter.jobs()) == 1
    assert fresh(second).preview == Preview.PENDING
    converter.answer()
    previews.pump()
    assert fresh(first).preview == Preview.READY
    assert fresh(second).preview == Preview.CONVERTING
    assert len(converter.jobs()) == 1


def test_a_pdf_answer_becomes_the_versions_pages(converter: Converter) -> None:
    version = upload(Document.objects.create(title="Board deck"), make_pptx(4))
    previews.pump()
    converter.answer(make_pdf(*[(720, 540)] * 4))
    previews.pump()
    version = fresh(version)
    assert version.preview == Preview.READY
    assert (version.page_count, version.page_sizes) == (4, [[720, 540]] * 4)
    assert previews.preview_path(version).read_bytes().startswith(b"%PDF-")
    # The job and its answer are cleared, so the next conversion sees nothing of this one.
    assert converter.jobs() == []
    assert list(converter.outbox.iterdir()) == []


def test_an_error_answer_leaves_the_file_shared_as_a_download(converter: Converter) -> None:
    version = upload(Document.objects.create(title="Board deck"), make_pptx(3))
    previews.pump()
    converter.answer(error="timeout")
    previews.pump()
    version = fresh(version)
    assert (version.preview, version.preview_error) == (Preview.FAILED, "timeout")
    assert version.status == DocumentVersion.Status.READY
    assert version.page_count == 0


@pytest.mark.parametrize("answer", [b"%PDF-1.7 but nothing a PDF reader can open", b"<html>"])
def test_a_bad_pdf_is_refused_by_snugs_own_worker(converter: Converter, answer: bytes) -> None:
    version = upload(Document.objects.create(title="Board deck"), make_pptx(3))
    previews.pump()
    converter.answer(answer)
    previews.pump()
    version = fresh(version)
    assert version.preview == Preview.FAILED
    assert not previews.preview_path(version).exists()


def test_an_answer_that_is_a_link_is_not_followed(converter: Converter, tmp_path: Path) -> None:
    """The converter runs LibreOffice on hostile files, so its answers are untrusted too."""
    version = upload(Document.objects.create(title="Board deck"), make_pptx(3))
    previews.pump()
    (job,) = converter.jobs()
    elsewhere = tmp_path / "elsewhere.pdf"
    elsewhere.write_bytes(make_pdf((720, 540)))
    (converter.outbox / f"{job.name}.pdf").symlink_to(elsewhere)
    previews.pump()
    assert fresh(version).preview == Preview.FAILED


def test_an_error_code_snug_does_not_know_is_read_as_unreadable(converter: Converter) -> None:
    version = upload(Document.objects.create(title="Board deck"), make_pptx(3))
    previews.pump()
    converter.answer(error="<script>")
    previews.pump()
    assert fresh(version).preview_error == "unreadable"


def test_a_converter_that_never_answers_is_given_up_on(converter: Converter) -> None:
    version = upload(Document.objects.create(title="Board deck"), make_pptx(3))
    previews.pump()
    later = timezone.now() + previews.STALE + timedelta(seconds=1)
    previews.pump(now=later)
    version = fresh(version)
    assert (version.preview, version.preview_error) == (Preview.FAILED, "no_answer")
    assert converter.jobs() == []


def test_stray_answers_are_cleared_but_the_converters_status_kept(converter: Converter) -> None:
    (converter.outbox / ("x" * 32 + ".pdf")).write_bytes(b"%PDF- left over")
    (converter.outbox / "status.json").write_text("{}")
    previews.pump()
    assert sorted(p.name for p in converter.outbox.iterdir()) == ["status.json"]


def test_while_another_process_runs_the_queue_this_one_leaves_it_alone(
    converter: Converter,
) -> None:
    version = upload(Document.objects.create(title="Board deck"), make_pptx(3))
    lock = settings.CONFIG.data_dir / "office.lock"
    with lock.open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        previews.pump()
    assert fresh(version).preview == Preview.PENDING
    assert converter.jobs() == []


# ---------------------------------------------------------------- new versions wait


def test_a_new_version_waits_for_its_preview_before_links_show_it(converter: Converter) -> None:
    deck = Document.objects.create(title="Board deck")
    first = upload(deck, make_pptx(3))
    previews.pump()
    converter.answer()
    previews.pump()
    second = upload(deck, make_pptx(5))
    assert second.status == DocumentVersion.Status.PREPARING
    assert deck.latest_version() == fresh(first)
    previews.pump()
    converter.answer(make_pdf(*[(720, 540)] * 5))
    previews.pump()
    assert deck.latest_version() == fresh(second)
    assert fresh(second).page_count == 5


def test_a_new_version_whose_preview_fails_is_shown_all_the_same(converter: Converter) -> None:
    deck = Document.objects.create(title="Board deck")
    upload(deck, make_pptx(3))
    second = upload(deck, make_pptx(5))
    previews.pump()
    converter.answer()  # the first version's job
    previews.pump()
    converter.answer(error="unreadable")
    previews.pump()
    assert deck.latest_version() == fresh(second)


# ---------------------------------------------------------------- without the converter


def test_without_the_converter_nothing_is_queued(data_folder: Path) -> None:
    version = upload(Document.objects.create(title="Board deck"), make_pptx(3))
    assert version.preview == Preview.NONE
    assert version.status == DocumentVersion.Status.READY


def test_a_pdf_is_never_queued(converter: Converter) -> None:
    version = upload(Document.objects.create(title="Seed deck"), make_pdf((960, 540)), "Deck.pdf")
    assert version.preview == Preview.NONE


def test_if_the_converter_is_removed_waiting_versions_are_shown(
    converter: Converter, monkeypatch: pytest.MonkeyPatch
) -> None:
    deck = Document.objects.create(title="Board deck")
    upload(deck, make_pptx(3))
    second = upload(deck, make_pptx(5))
    monkeypatch.setattr(settings, "CONFIG", dataclasses.replace(settings.CONFIG, office=False))
    previews.pump()
    second = fresh(second)
    assert (second.preview, second.status) == (Preview.NONE, DocumentVersion.Status.READY)
    assert deck.latest_version() == second
    assert list(converter.inbox.iterdir()) == []


# ---------------------------------------------------------------- read as pages

INVESTOR = "priya@northfield.example"


def previewed(converter: Converter, pages: int = 4) -> Document:
    deck = Document.objects.create(title="Board deck")
    upload(deck, make_pptx(pages))
    previews.pump()
    converter.answer(make_pdf(*[(720, 540)] * pages))
    previews.pump()
    return deck


def link_to(document: Document, **options: Any) -> service.Issued:
    fields: dict[str, Any] = {
        "email": INVESTOR,
        "note": "",
        "expires_at": None,
        "require_verification": False,
        "allow_download": True,
        "created_by": None,
    }
    fields.update(options)
    return service.create(document=document, **fields)


def opened(issued: service.Issued) -> Client:
    browser = Client()
    browser.post(f"/v/{issued.token}/open", secure=True)
    return browser


def test_a_previewed_deck_is_read_page_by_page(converter: Converter) -> None:
    deck = previewed(converter, pages=4)
    issued = link_to(deck)
    browser = opened(issued)
    page = browser.get(f"/v/{issued.token}", secure=True).content.decode()
    assert "1 of 4" in page or "of 4" in page
    assert f"/v/{issued.token}/pages/1-" in page
    assert "Board deck, a PowerPoint presentation, 4 pages" in page
    assert "<dt>Access</dt><dd>View in the browser, or download</dd>" in page
    assert (
        "when you open this presentation, how long you spend on each page, and when you "
        "download it. Your email address is written into the document properties of any "
        "copy you download." in page
    )
    image = browser.get(f"/v/{issued.token}/pages/2-800.webp", secure=True)
    assert image.status_code == 200 and image["Content-Type"] == "image/webp"


def test_the_download_of_a_previewed_deck_is_still_the_stamped_original(
    converter: Converter,
) -> None:
    deck = previewed(converter)
    issued = link_to(deck)
    response = opened(issued).get(f"/v/{issued.token}/download", secure=True)
    assert response["Content-Type"] == OFFICE["pptx"].content_type
    assert 'filename="Board deck.pptx"' in response["Content-Disposition"]
    assert custom_properties(response.content)["Shared with"] == INVESTOR


def test_until_its_preview_is_ready_a_deck_shows_its_card(converter: Converter) -> None:
    deck = Document.objects.create(title="Board deck")
    upload(deck, make_pptx(3))
    issued = link_to(deck)
    browser = opened(issued)
    page = browser.get(f"/v/{issued.token}", secure=True).content.decode()
    assert "Download the presentation" in page and "/pages/1-" not in page
    assert browser.get(f"/v/{issued.token}/pages/1-800.webp", secure=True).status_code == 404


def test_without_the_converter_a_previewed_deck_shows_its_card_again(
    converter: Converter, monkeypatch: pytest.MonkeyPatch
) -> None:
    deck = previewed(converter)
    monkeypatch.setattr(settings, "CONFIG", dataclasses.replace(settings.CONFIG, office=False))
    issued = link_to(deck)
    browser = opened(issued)
    page = browser.get(f"/v/{issued.token}", secure=True).content.decode()
    assert "Download the presentation" in page and "/pages/1-" not in page
    assert browser.get(f"/v/{issued.token}/pages/1-800.webp", secure=True).status_code == 404


def test_reading_a_previewed_deck_is_counted_page_by_page(converter: Converter) -> None:
    deck = previewed(converter, pages=4)
    issued = link_to(deck)
    opened(issued).get(f"/v/{issued.token}?page=3", secure=True)
    (person,) = summary.for_people()
    (read,) = person.documents
    assert read.card == "" and read.pages == 4


def test_the_founders_description_is_shown_beside_a_previewed_deck(converter: Converter) -> None:
    deck = previewed(converter)
    deck.description = "Start at slide 3."
    deck.save()
    issued = link_to(deck)
    page = opened(issued).get(f"/v/{issued.token}", secure=True).content.decode()
    about = page[page.index('<aside class="about"') :]
    assert "Start at slide 3." in about


def test_the_admin_sees_a_previewed_decks_first_page_and_reading(
    converter: Converter, admin_browser: Client
) -> None:
    deck = previewed(converter, pages=4)
    version = deck.latest_version()
    assert version is not None
    first = f"/admin/documents/{deck.token}/v{version.number}/page-1.webp"
    page = admin_browser.get(f"/admin/documents/{deck.token}", secure=True).content.decode()
    assert first in page
    assert "Nobody has opened this document yet. Reading time appears here" in page
    response = admin_browser.get(first, secure=True)
    response.close()  # a file response: closed, so its file is too
    assert (response.status_code, response["Content-Type"]) == (200, "image/webp")


# ---------------------------------------------------------------- files from before the converter


def test_office_files_from_before_the_converter_are_queued_once_it_is_installed(
    data_folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deck = Document.objects.create(title="Board deck")
    old = upload(deck, make_pptx(3), "Deck v1.pptx")
    current = upload(deck, make_pptx(4), "Deck v2.pptx")
    assert current.preview == Preview.NONE  # uploaded before LibreOffice was installed
    office = data_folder / "office"
    fake = Converter(office)
    monkeypatch.setattr(
        settings, "CONFIG", dataclasses.replace(settings.CONFIG, office=True, office_dir=office)
    )
    previews.pump()
    assert fresh(current).preview == Preview.CONVERTING  # the version links show
    assert fresh(old).preview == Preview.NONE  # an earlier one is not worth converting
    fake.answer(make_pdf(*[(720, 540)] * 4))
    previews.pump()
    assert fresh(current).preview == Preview.READY


def test_a_failed_preview_is_not_queued_again_by_itself(converter: Converter) -> None:
    version = upload(Document.objects.create(title="Board deck"), make_pptx(3))
    previews.pump()
    converter.answer(error="unreadable")
    previews.pump()
    previews.pump()
    assert fresh(version).preview == Preview.FAILED
    assert converter.jobs() == []


# ---------------------------------------------------------------- after a restore


def test_a_preview_lost_in_a_restore_is_made_again(converter: Converter) -> None:
    # Previews are not backed up. Found 13 November 2026: after a restore onto
    # a new server, a version marked ready had no PDF, and nothing remade it.
    deck = previewed(converter, pages=4)
    version = deck.latest_version()
    assert version is not None and version.reads_as_pages
    previews.preview_path(version).unlink()
    previews.pump()  # housekeeping runs this as Snug starts, then every hour
    version = fresh(version)
    assert version.preview in (Preview.PENDING, Preview.CONVERTING)
    assert not version.reads_as_pages  # a card meanwhile, never a broken page
    converter.answer(make_pdf(*[(720, 540)] * 4))
    previews.pump()
    assert fresh(version).reads_as_pages


def test_a_page_whose_pdf_has_gone_says_it_cannot_be_shown(converter: Converter) -> None:
    deck = previewed(converter, pages=4)
    version = deck.latest_version()
    assert version is not None
    previews.preview_path(version).unlink()
    issued = link_to(deck)
    response = opened(issued).get(f"/v/{issued.token}/pages/1-800.webp", secure=True)
    assert response.status_code == 503
