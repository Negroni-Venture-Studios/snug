"""Settings shows when the backups last reached the bucket, and every section links its guide."""

import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from django.test import Client

from snug.accounts import sessions
from snug.accounts.models import Admin

pytestmark = pytest.mark.django_db


@pytest.fixture
def client() -> Client:
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser


def backups_section(client: Client) -> str:
    page = client.get("/admin/settings/", secure=True).content.decode()
    start = page.index('id="backups"')
    # Django joins a number to its unit with a non-breaking space.
    return page[start : page.index("</main>", start)].replace("\xa0", " ")


def record(data_folder: Path, name: str, text: str) -> None:
    (data_folder / "backup").mkdir(exist_ok=True)
    (data_folder / "backup" / name).write_text(text, encoding="utf-8")


def test_a_recent_copy_shows_working_with_its_time(client: Client, data_folder: Path) -> None:
    copied = datetime.now(UTC) - timedelta(minutes=3)
    record(data_folder, "last-success", copied.strftime("%Y-%m-%dT%H:%M:%SZ"))
    section = backups_section(client)
    assert '<span class="status status-ok">Working</span>' in section
    assert "Last copied to the bucket 3 minutes ago." in section
    local = copied.astimezone(ZoneInfo("Europe/London"))
    assert f"finished at {local:%H:%M} on {local.day} {local:%B %Y}." in section


def test_a_problem_shows_its_reason(client: Client, data_folder: Path) -> None:
    copied = datetime.now(UTC) - timedelta(minutes=7)
    record(data_folder, "last-success", copied.strftime("%Y-%m-%dT%H:%M:%SZ"))
    record(data_folder, "problem", "The bucket refused the key.")
    section = backups_section(client)
    assert '<span class="status status-warn">Problem</span>' in section
    assert "The bucket refused the key. The last complete copy was 7 minutes ago." in section


def test_no_copy_yet_says_so(client: Client, data_folder: Path) -> None:
    section = backups_section(client)
    assert '<span class="status status-warn">Problem</span>' in section
    assert "Nothing has been backed up yet." in section


@pytest.mark.usefixtures("data_folder")
def test_the_backups_section_links_its_guide(client: Client) -> None:
    assert 'docs/backups.md"' in backups_section(client)


@pytest.mark.usefixtures("data_folder")
def test_every_settings_section_links_a_guide(client: Client) -> None:
    page = client.get("/admin/settings/", secure=True).content.decode()
    heads = re.findall(r'<div class="section-head[^"]*"><h2>(.*?)</h2>(.*?)</div>', page, re.DOTALL)
    assert {name for name, _ in heads} >= {
        "Admins",
        "Your account",
        "Email",
        "Branding",
        "NDA",
        "Privacy",
        "Backups",
    }
    unlinked = [name for name, head in heads if 'class="doc-link' not in head]
    assert unlinked == []


def test_a_problem_links_the_guide_beside_it(client: Client, data_folder: Path) -> None:
    record(data_folder, "problem", "Backups are not set up. Run sudo snug setup to add a bucket.")
    section = backups_section(client)
    panel = section[section.index('class="panel backup-status"') :]
    assert "Backups are not set up. Run sudo snug setup to add a bucket." in panel
    assert 'docs/backups.md"' in panel
    assert "explains how to set backups up" in panel


def test_working_backups_need_no_second_link(client: Client, data_folder: Path) -> None:
    record(data_folder, "last-success", datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    section = backups_section(client)
    panel = section[section.index('class="panel backup-status"') :]
    assert 'docs/backups.md"' not in panel
