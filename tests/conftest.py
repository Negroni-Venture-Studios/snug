"""Shared fixtures."""

import dataclasses
import os
import time
import types
from pathlib import Path

import pytest
from django.conf import settings

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
