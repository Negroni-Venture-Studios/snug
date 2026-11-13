"""The Office files panel in Settings, and the converter's status behind it.

Decided 12 November 2026: the panel sits between Branding and NDA. Without
LibreOffice it says so, with the command that installs it. With it, it
shows the LibreOffice version and when the converter's image was built,
says when an update is waiting (the image is rebuilt every week, so one
more than 14 days old has missed a rebuild; Snug makes no call out to
find out), and has a toggle for showing Office files in the browser.
"""

import json
from datetime import timedelta
from pathlib import Path

import pytest
from django.test import Client
from django.utils import timezone

from snug.audit.models import AuditEvent
from snug.core import doctor
from snug.core.models import SiteSettings
from snug.documents.models import Document
from snug.office import previews
from tests.fake_converter import Converter
from tests.office_files import make_pptx
from tests.pdfs import make_pdf
from tests.test_office_previews import upload

pytestmark = pytest.mark.django_db


def report(converter: Converter, *, built_days_ago: int = 3, heard_minutes_ago: int = 0) -> None:
    now = timezone.now()
    (converter.outbox / "status.json").write_text(
        json.dumps(
            {
                "libreoffice": "4:25.2.3-2+deb13u9",
                "built": (now - timedelta(days=built_days_ago)).date().isoformat(),
                "started": now.isoformat(),
                "heartbeat": (now - timedelta(minutes=heard_minutes_ago)).isoformat(),
                "kernel": "4.19.0",
                "seccomp": "unix sockets only",
            }
        )
    )


def office_section(browser: Client) -> str:
    page = browser.get("/admin/settings/", secure=True).content.decode()
    start = page.index('id="office"')
    return page[start : page.index('id="nda"')]


def test_the_panel_sits_between_branding_and_the_nda(admin_browser: Client) -> None:
    page = admin_browser.get("/admin/settings/", secure=True).content.decode()
    assert page.index('id="branding"') < page.index('id="office"') < page.index('id="nda"')


def test_without_libreoffice_the_panel_says_how_to_install_it(admin_browser: Client) -> None:
    section = office_section(admin_browser)
    assert "Not installed" in section
    assert "sudo snug update --with-office" in section
    assert "office.md" in section


def test_a_working_converter_shows_its_version_and_age(
    converter: Converter, admin_browser: Client
) -> None:
    report(converter, built_days_ago=3)
    section = office_section(admin_browser)
    assert "Working" in section
    assert "LibreOffice 25.2.3" in section
    assert "An update is waiting" not in section
    assert 'name="show" checked' in section


def test_an_old_image_says_an_update_is_waiting(
    converter: Converter, admin_browser: Client
) -> None:
    report(converter, built_days_ago=20)
    section = office_section(admin_browser)
    assert "An update is waiting" in section
    assert "sudo snug update" in section


@pytest.mark.parametrize(
    ("setup", "said"),
    [
        (lambda c: None, "The converter has not started."),
        (
            lambda c: report(c, heard_minutes_ago=10),
            "The converter has not reported for 10 minutes.",
        ),
    ],
)
def test_a_converter_that_is_not_running_is_a_problem(
    converter: Converter, admin_browser: Client, setup: object, said: str
) -> None:
    setup(converter)  # type: ignore[operator]
    section = office_section(admin_browser)
    assert "Problem" in section and said in section


def test_the_converters_status_file_is_not_trusted(converter: Converter, tmp_path: Path) -> None:
    """The converter reads hostile files, so what it writes is read with care."""
    elsewhere = tmp_path / "elsewhere.json"
    heard = timezone.now().isoformat()
    elsewhere.write_text(json.dumps({"libreoffice": "1.0", "heartbeat": heard}))
    (converter.outbox / "status.json").symlink_to(elsewhere)
    assert previews.converter_status() is None
    (converter.outbox / "status.json").unlink()
    (converter.outbox / "status.json").write_text(
        json.dumps({"libreoffice": "<b>" + "x" * 500, "built": "not a date", "heartbeat": 7})
    )
    status = previews.converter_status()
    assert status is not None
    assert status.libreoffice == "" and status.built is None and status.heartbeat is None


def test_turning_previews_off_shows_office_files_as_downloads(
    converter: Converter, admin_browser: Client
) -> None:
    deck = Document.objects.create(title="Board deck")
    upload(deck, make_pptx(3))
    previews.pump()
    converter.answer(make_pdf(*[(720, 540)] * 3))
    previews.pump()
    version = deck.latest_version()
    assert version is not None and version.reads_as_pages
    response = admin_browser.post("/admin/settings/office", {}, secure=True)
    assert response["Location"] == "/admin/settings/?saved=office#office"
    assert SiteSettings.load().office_previews is False
    assert not Document.objects.get(pk=deck.pk).latest_version().reads_as_pages  # type: ignore[union-attr]
    event = AuditEvent.objects.get(action="settings.office_previews_changed")
    assert event.details == {"show": False}
    shown = admin_browser.get(response["Location"], secure=True).content.decode()
    assert "Saved. Word, PowerPoint and Excel files are shared as downloads only." in shown
    admin_browser.post("/admin/settings/office", {"show": "on"}, secure=True)
    assert SiteSettings.load().office_previews is True


# ---------------------------------------------------------------- snug doctor


def test_doctor_skips_the_converter_when_it_is_not_installed() -> None:
    finding = doctor.office(timezone.now())
    assert (finding.label, finding.status) == ("Office converter", "skipped")


def test_doctor_reports_a_working_converter(converter: Converter) -> None:
    report(converter)
    finding = doctor.office(timezone.now())
    assert finding.status == "ok"
    assert "LibreOffice 25.2.3" in finding.message


@pytest.mark.parametrize(
    ("days", "minutes", "said"),
    [(20, 0, "sudo snug update"), (3, 10, "has not reported")],
)
def test_doctor_reports_a_problem_with_the_converter(
    converter: Converter, days: int, minutes: int, said: str
) -> None:
    report(converter, built_days_ago=days, heard_minutes_ago=minutes)
    finding = doctor.office(timezone.now())
    assert finding.status == "problem" and said in finding.message
