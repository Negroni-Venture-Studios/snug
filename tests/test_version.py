"""Which Snug is running: a release, an edge build or a source checkout, shown on the Overview."""

import time

import pytest
from django.test import Client

from snug import __version__
from snug.accounts import sessions
from snug.accounts.models import Admin
from snug.core import version

COMMIT = "3e7eeab0c9d2f1a4b5e6f708192a3b4c5d6e7f80"


@pytest.fixture
def no_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SNUG_BUILD_VERSION", raising=False)
    monkeypatch.delenv("SNUG_BUILD_COMMIT", raising=False)


def build(monkeypatch: pytest.MonkeyPatch, name: str, commit: str = COMMIT) -> None:
    monkeypatch.setenv("SNUG_BUILD_VERSION", name)
    monkeypatch.setenv("SNUG_BUILD_COMMIT", commit)


def test_a_release_shows_its_version(monkeypatch: pytest.MonkeyPatch) -> None:
    build(monkeypatch, "1.4.2")
    running = version.running()
    assert running.name == "Snug 1.4.2"
    assert running.release == "1.4.2"
    assert running.commit_url == ""


def test_a_release_candidate_is_a_release_of_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    build(monkeypatch, "1.4.2-rc.1")
    assert version.running().name == "Snug 1.4.2-rc.1"
    assert version.running().release == "1.4.2-rc.1"


def test_an_edge_build_shows_its_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    build(monkeypatch, "edge")
    running = version.running()
    assert running.name == "Snug edge, commit 3e7eeab"
    assert running.release == ""
    assert running.commit_url == (
        f"https://github.com/Negroni-Venture-Studios/snug/commit/{COMMIT}"
    )


@pytest.mark.usefixtures("no_build")
def test_a_source_checkout_says_so() -> None:
    running = version.running()
    assert running.name == f"Snug {__version__}, from source"
    assert running.release == ""


def test_the_guides_follow_a_release(monkeypatch: pytest.MonkeyPatch) -> None:
    build(monkeypatch, "1.4.2")
    assert version.docs_url() == "https://github.com/Negroni-Venture-Studios/snug/blob/v1.4.2/docs/"


def test_the_guides_are_mains_otherwise(monkeypatch: pytest.MonkeyPatch) -> None:
    build(monkeypatch, "edge")
    assert version.docs_url() == "https://github.com/Negroni-Venture-Studios/snug/blob/main/docs/"


@pytest.mark.django_db
def test_the_overview_shows_the_version_and_how_to_update(monkeypatch: pytest.MonkeyPatch) -> None:
    build(monkeypatch, "1.4.2")
    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    page = browser.get("/admin/", secure=True).content.decode()
    start = page.index('<p class="system-version">')
    line = page[start : page.index("</p>", start)]
    assert "Snug 1.4.2." in line
    assert (
        '<a class="doc-link" href="https://github.com/Negroni-Venture-Studios/snug/blob/v1.4.2/docs/'
        'deploy-digitalocean.md#keeping-snug-up-to-date"' in line
    )
    assert "<svg" in line  # the book icon every guide link has
    assert page.index("system-status") < start  # under the status line
