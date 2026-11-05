"""A real browser against a real server: Playwright and pytest-django's live server."""

from collections.abc import Iterator
from typing import Any

import pytest
from django.core.management import call_command
from django.db import connection
from playwright.sync_api import ConsoleMessage, Page, sync_playwright


@pytest.fixture(scope="session", autouse=True)
def static_files() -> None:
    """WhiteNoise serves the collected files, as in production."""
    call_command("collectstatic", interactive=False, verbosity=0)


@pytest.fixture(autouse=True)
def flushable_audit_log(transactional_db: None) -> Iterator[None]:
    """Let pytest-django empty the database between live-server tests.

    The audit log refuses deletes, by design, through triggers. A live server
    needs committed data, which pytest-django clears by deleting every row
    after each test, so the triggers are lifted for that moment only.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'audit_auditevent'"
        )
        triggers = [row[0] for row in cursor.fetchall()]
    yield
    with connection.cursor() as cursor:
        for name in ("audit_auditevent_no_update", "audit_auditevent_no_delete"):
            cursor.execute(f"DROP TRIGGER IF EXISTS {name}")
    # Restored after the flush, by the next test's setup.
    _PENDING_TRIGGERS[:] = triggers


_PENDING_TRIGGERS: list[str] = []


@pytest.fixture(autouse=True)
def restore_audit_triggers(transactional_db: None) -> None:
    with connection.cursor() as cursor:
        for sql in _PENDING_TRIGGERS:
            cursor.execute(sql)
    _PENDING_TRIGGERS.clear()


@pytest.fixture(scope="session")
def browser() -> Iterator[Any]:
    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch()
        yield chromium
        chromium.close()


@pytest.fixture
def page(browser: Any) -> Iterator[Page]:
    """A fresh page that fails the test on any console error.

    The CSP reports a blocked inline script or style as a console error, and
    a dead click often leaves one too: neither can hide here.
    """
    context = browser.new_context(viewport={"width": 390, "height": 844})
    tab = context.new_page()
    errors: list[str] = []

    def record(message: ConsoleMessage) -> None:
        if message.type == "error":
            errors.append(message.text)

    tab.on("console", record)
    tab.on("pageerror", lambda error: errors.append(str(error)))
    yield tab
    context.close()
    # 404 and 4xx pages are expected in some tests; anything else is not.
    unexpected = [e for e in errors if "status of 4" not in e]
    assert not unexpected, unexpected
