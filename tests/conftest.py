"""Shared fixtures."""

import dataclasses
import os
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
