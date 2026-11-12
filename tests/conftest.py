"""Shared fixtures."""

import dataclasses
import os
import time
import types
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from django.conf import settings

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable

    from django.test import Client

    from snug.documents.models import DocumentVersion
    from tests.fake_converter import Converter

# The end-to-end tests need a browser and their own settings: see tests/e2e_settings.py.
collect_ignore = [] if os.environ.get("SNUG_E2E") == "1" else ["e2e"]


@pytest.fixture
def data_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh data folder for this test. Files outlive the database rollback."""
    for folder in ("originals", "renders", "tmp"):
        (tmp_path / folder).mkdir()
    monkeypatch.setattr(settings, "CONFIG", dataclasses.replace(settings.CONFIG, data_dir=tmp_path))
    return tmp_path


@pytest.fixture
def email_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure SMTP. pytest-django swaps in Django's in-memory mail backend."""
    monkeypatch.setattr(
        settings,
        "CONFIG",
        dataclasses.replace(
            settings.CONFIG, smtp_host="smtp.example.com", email_from="rooms@fernlea.example"
        ),
    )


@pytest.fixture(autouse=True)
def no_network_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Housekeeping fetches DB-IP's data; no test may reach the internet for it, or Google."""
    import urllib.request

    from snug.core import outbound

    def refuse(*args: object, **kwargs: object) -> None:
        raise OSError("tests do not download")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(outbound, "urlopen", refuse)


@pytest.fixture(scope="session")
def real_warm_in_background() -> "Callable[[DocumentVersion], threading.Thread | None]":
    """The real background render, for the test of it: every other test stops it."""
    from snug.rendering import pages

    return pages.warm_in_background


@pytest.fixture(autouse=True)
def no_background_rendering(monkeypatch: pytest.MonkeyPatch) -> None:
    """Page 1 is rendered in a thread once a version can be read; in tests, it is not.

    The thread would outlive its test and render into a data folder that is gone.
    """
    from snug.rendering import pages

    monkeypatch.setattr(pages, "warm_in_background", lambda version: None)


@pytest.fixture(autouse=True)
def steady_rate_limit_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rate limits count in fixed windows on the clock (15 minutes for sign-in).

    A test that fills a window and then expects a refusal fails if the clock
    crosses a window boundary in between, which CI did at 09:30:00 on
    6 November 2026. The limiter's clock is held at the test's start; tests
    of window expiry pass explicit times, so they are unaffected.
    """
    from snug.core import ratelimit

    started = time.time()
    monkeypatch.setattr(ratelimit, "time", types.SimpleNamespace(time=lambda: started))


@pytest.fixture
def converter(data_folder: Path, monkeypatch: pytest.MonkeyPatch) -> "Converter":
    """The LibreOffice converter installed, with a stand-in that answers when told to."""
    from tests.fake_converter import Converter

    office = data_folder / "office"
    fake = Converter(office)
    monkeypatch.setattr(
        settings, "CONFIG", dataclasses.replace(settings.CONFIG, office=True, office_dir=office)
    )
    return fake


@pytest.fixture
def admin_browser(db: None) -> "Client":
    """A browser signed in as an admin, past the second factor."""
    from django.test import Client

    from snug.accounts import sessions
    from snug.accounts.models import Admin

    admin = Admin.objects.create(email="ruth@fernlea.example", is_active=True)
    browser = Client()
    browser.force_login(admin)
    session = browser.session
    session[sessions.LOGIN_AT] = session[sessions.LAST_ACTIVITY] = time.time()
    session.save()
    return browser
